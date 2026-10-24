"""Playlists, their fields, which Playlist a Work is in, and their order, on PostgreSQL (L-O-1 #633).

The rules are ``backend/playlist_sync.py``'s, imported rather than copied:
the field vocabulary and bounds, the canonical spelling (``canonical_wire``,
``DEFAULT_TITLE``), the rule codes (``PlaylistRuleError``), the order rule
(``resolve_order``) and the scopes: one per field
(``playlist-field / ["PL-…","field"]``), one per Playlist for its order as
an aggregate (``playlist-order / PL-…``) and one per Work for the Playlist
it is in (``work-playlist / W-…``). Only a change advances a scope, and
construction (a new Playlist, a new Work filed into one) advances none.

A Work is in at most one Playlist (``playlist_items`` has one row per Work),
so membership is a scalar of the Work: adding, moving and removing are one
command with different values. A Work that joins or leaves a Playlist
changes that Playlist's order too, so membership advances both Playlists'
order revisions with the Work's own: a reorder made before the add is then
stale. The order is ``ORDER BY position, work_id``; an add appends at
``max(position) + 1`` and a reorder rewrites the positions ``0…n-1``, as on
SQLite.

What moves:

- **Every writer of a Playlist's order holds the Playlist row**, ``FOR NO
  KEY UPDATE``: a reorder, a Work joining or leaving it (also through a
  Work create or delete) and a field edit. They queue on the row, so the
  members and positions a writer reads after its lock are the committed
  ones, and two Works appended at once get two positions. A Playlist
  delete takes the row ``FOR UPDATE``, so it waits for them and they for it.
- **A deleted Playlist** releases its Works (each one's ``work-playlist``
  advances, as on SQLite), advances its order, and leaves every field scope
  one revision on (§5.2), a never-edited one too (SQLite leaves them): a
  client-minted id can be created again, and a base observed before the
  delete is then stale.
- **A deleted Work** leaves its Playlist explicitly, advancing that
  Playlist's order (SQLite's cascade advances nothing); its
  ``work-playlist`` scope is tombstoned with the Work's other scopes
  (``commands.work_scopes``).
- **A Playlist's** ``original_url`` is ``''`` when empty (SQLite stores
  NULL); the reads give NULL back.

**Lock order** (with the package order in ``backend.domain.organization``):

1. the Work row of a command that changes the Work's Playlist (``FOR NO KEY
   UPDATE``; ``FOR UPDATE`` for its delete);
2. Playlist rows, by id (``FOR UPDATE`` for a delete, ``FOR NO KEY UPDATE``
   otherwise);
3. revision scopes, sorted.

No Playlist command locks a Folder or a Tag, and the Work delete takes its
Playlist after leaving its Folder (which locks no Folder row).
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend import playlist_sync as rules
from backend import work_metadata_sync as meta
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import links
from backend.domain.works import revisions
from backend.playlist_sync import PlaylistRuleError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE
ORDER_SCOPE_TYPE = rules.ORDER_SCOPE_TYPE
WORK_SCOPE_TYPE = rules.WORK_SCOPE_TYPE

_COLUMNS: LiteralString = """
p.id, p.title, p.description, NULLIF(p.original_url, '') AS original_url,
to_char(p.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(p.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_PLAYLIST_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.playlists p WHERE p.library_id = %(library_id)s AND p.id = %(playlist_id)s"
)
# Counts are aggregated once and joined, never counted per row
# (scripts/check_correlated_catalog_counts.py).
_FROM_WITH_COUNTS: LiteralString = """
FROM prks.playlists p
LEFT JOIN (SELECT playlist_id, count(*) AS n FROM prks.playlist_items
           WHERE library_id = %(library_id)s GROUP BY playlist_id) ic ON ic.playlist_id = p.id
"""
# `get_all_playlists`: newest change first. SQLite's timestamps have whole
# seconds, so equal rendered values fall back to the id as they do there.
_CATALOG_SQL: LiteralString = (
    "SELECT " + _COLUMNS + ", COALESCE(ic.n, 0) AS item_count"
    + _FROM_WITH_COUNTS
    + " WHERE p.library_id = %(library_id)s"
    " ORDER BY date_trunc('second', p.updated_at) DESC, date_trunc('second', p.created_at) DESC,"
    " p.id COLLATE \"C\""
)
_ROW_WITH_COUNT_SQL: LiteralString = (
    "SELECT p.id, p.title, p.description, NULLIF(p.original_url, '') AS original_url,"
    " COALESCE(ic.n, 0) AS item_count"
    + _FROM_WITH_COUNTS
    + " WHERE p.library_id = %(library_id)s AND p.id = %(playlist_id)s"
)
_LOCK_SQL: LiteralString = """
SELECT id FROM prks.playlists WHERE library_id = %(library_id)s AND id = %(playlist_id)s FOR NO KEY UPDATE
"""
_LOCK_KEY_SQL: LiteralString = """
SELECT id FROM prks.playlists WHERE library_id = %(library_id)s AND id = %(playlist_id)s FOR UPDATE
"""
_SHARE_SQL: LiteralString = """
SELECT id FROM prks.playlists WHERE library_id = %(library_id)s AND id = %(playlist_id)s FOR KEY SHARE
"""
_EXISTS_SQL: LiteralString = "SELECT 1 FROM prks.playlists WHERE library_id = %(library_id)s AND id = %(playlist_id)s"
_INSERT_SQL: LiteralString = """
INSERT INTO prks.playlists (library_id, id, title, description, original_url)
VALUES (%(library_id)s, %(playlist_id)s, %(title)s, %(description)s, %(original_url)s)
ON CONFLICT DO NOTHING
RETURNING id
"""
_SET_SQL: dict[str, LiteralString] = {
    "title": "UPDATE prks.playlists SET title = %(value)s, updated_at = now()"
             " WHERE library_id = %(library_id)s AND id = %(playlist_id)s",
    "description": "UPDATE prks.playlists SET description = %(value)s, updated_at = now()"
                   " WHERE library_id = %(library_id)s AND id = %(playlist_id)s",
    "original_url": "UPDATE prks.playlists SET original_url = %(value)s, updated_at = now()"
                    " WHERE library_id = %(library_id)s AND id = %(playlist_id)s",
}
_TOUCH_SQL: LiteralString = """
UPDATE prks.playlists SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(playlist_id)s
"""
_ORDER_SQL: LiteralString = """
SELECT work_id FROM prks.playlist_items WHERE library_id = %(library_id)s AND playlist_id = %(playlist_id)s
ORDER BY position, work_id COLLATE "C"
"""
_WORK_PLAYLIST_SQL: LiteralString = """
SELECT playlist_id FROM prks.playlist_items WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_REMOVE_ITEM_SQL: LiteralString = """
DELETE FROM prks.playlist_items WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_APPEND_SQL: LiteralString = """
INSERT INTO prks.playlist_items (library_id, work_id, playlist_id, position)
SELECT %(library_id)s, %(work_id)s, %(playlist_id)s, COALESCE(max(position), -1) + 1
FROM prks.playlist_items WHERE library_id = %(library_id)s AND playlist_id = %(playlist_id)s
"""
_REPOSITION_SQL: LiteralString = """
UPDATE prks.playlist_items i SET position = o.n - 1
FROM unnest(%(work_ids)s::text[]) WITH ORDINALITY AS o(work_id, n)
WHERE i.library_id = %(library_id)s AND i.playlist_id = %(playlist_id)s AND i.work_id = o.work_id
"""
_REMOVE_ITEMS_SQL: LiteralString = """
DELETE FROM prks.playlist_items WHERE library_id = %(library_id)s AND playlist_id = %(playlist_id)s
"""
_DELETE_SQL: LiteralString = "DELETE FROM prks.playlists WHERE library_id = %(library_id)s AND id = %(playlist_id)s"
_WORK_EXISTS_SQL: LiteralString = "SELECT 1 FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"


class ForeignPlaylistId(Exception):
    """The id is a Playlist of another Library."""


# -- locks and small reads ------------------------------------------------------


def lock(scope: LibraryScope, playlist_id: str, *, key: bool = False) -> bool:
    """Step 2: hold a Playlist a command changes (``FOR UPDATE`` when ``key``); False when absent."""
    sql = _LOCK_KEY_SQL if key else _LOCK_SQL
    return scope.execute(sql, {"playlist_id": playlist_id}).fetchone() is not None


def share(scope: LibraryScope, playlist_id: str) -> bool:
    """Hold a Playlist against its delete without changing it (a converged create)."""
    return scope.execute(_SHARE_SQL, {"playlist_id": playlist_id}).fetchone() is not None


def exists(scope: LibraryScope, playlist_id: str) -> bool:
    return scope.execute(_EXISTS_SQL, {"playlist_id": playlist_id}).fetchone() is not None


def playlist_row(scope: LibraryScope, playlist_id: str) -> Optional[dict[str, Any]]:
    """The Playlist row (``SELECT * FROM playlists`` on SQLite), or None."""
    row = scope.execute(_PLAYLIST_SQL, {"playlist_id": playlist_id}).fetchone()
    return None if row is None else dict(row)


def order(scope: LibraryScope, playlist_id: str) -> list[str]:
    """``current_order``: the member Works, in order."""
    return [str(row["work_id"]) for row in scope.execute(_ORDER_SQL, {"playlist_id": playlist_id})]


def work_playlist(scope: LibraryScope, work_id: str) -> str:
    """``current_work_playlist``: the Work's Playlist id, ``""`` for none."""
    row = scope.execute(_WORK_PLAYLIST_SQL, {"work_id": work_id}).fetchone()
    return "" if row is None else str(row["playlist_id"])


def _validate(field: str, value: Any) -> str:
    text = rules.validate_field_value(field, value)
    if "\x00" in text:
        raise PlaylistRuleError("INVALID_ENVELOPE", "field values cannot contain NUL")
    return text


def _current_field(scope: LibraryScope, playlist_id: str, field: str) -> Optional[str]:
    row = playlist_row(scope, playlist_id)
    if row is None:
        return None
    value = row[field]
    return "" if value is None else str(value)


def rule_status(code: str) -> int:
    """The HTTP status of a ``PlaylistRuleError`` code in a terminal result (``_rule_status``)."""
    return 404 if code in ("ENTITY_NOT_FOUND", "PLAYLIST_NOT_FOUND") else 409


def _touch(scope: LibraryScope, playlist_id: str) -> None:
    scope.execute(_TOUCH_SQL, {"playlist_id": playlist_id})


# -- create ---------------------------------------------------------------------


def insert(scope: LibraryScope, playlist_id: str, title: Any, description: Any = "",
           original_url: Any = "") -> bool:
    """``insert_playlist_on_conn``: construction, every scope at revision 0.

    Returns False when the id is already a Playlist of this Library (a
    concurrent create of it committed first); that Playlist is then held
    ``FOR KEY SHARE`` until COMMIT, like one found before inserting. Raises
    ``PlaylistRuleError`` (``INVALID_ENVELOPE``) with nothing inserted, and
    :class:`ForeignPlaylistId` for an id of another Library.
    """
    values = {
        "playlist_id": playlist_id,
        "title": _validate("title", title),
        "description": _validate("description", description),
        "original_url": _validate("original_url", original_url),
    }
    # Twice: the winner of a same-id race can be deleted between its COMMIT
    # and this hold, and then the id is free again. The insert has no
    # conflict target, so a race lost on either unique index waits for the
    # winner and then does nothing.
    for _ in range(2):
        if scope.execute(_INSERT_SQL, values).fetchone() is not None:
            return True
        if share(scope, playlist_id):
            return False
    raise ForeignPlaylistId(playlist_id)


def _row_with_count(scope: LibraryScope, playlist_id: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_ROW_WITH_COUNT_SQL, {"playlist_id": playlist_id}).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["item_count"] = int(out["item_count"])
    return out


def apply_create(scope: LibraryScope, playlist_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``CREATE_PLAYLIST``'s ``apply_create``: SQLite's results.

    A create of an id that already exists acknowledges the stored Playlist
    (``changed: false``) with its item count. Raises
    :class:`ForeignPlaylistId` for an id of another Library.
    """
    existing = share(scope, playlist_id)
    if not existing:
        existing = not insert(scope, playlist_id, payload["title"], payload["description"],
                              payload["original_url"])
    return 200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id, "changed": not existing,
                 "playlist": _row_with_count(scope, playlist_id)}


# -- fields ---------------------------------------------------------------------


def set_field(scope: LibraryScope, playlist_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together.

    Returns ``(changed, revision_after)``; raises ``PlaylistRuleError`` for an
    invalid value or a missing Playlist.
    """
    desired = _validate(field, value)
    if not lock(scope, playlist_id):
        raise PlaylistRuleError("ENTITY_NOT_FOUND", "Playlist not found.")
    scope_id = rules.scope_key(playlist_id, field)
    revision = revisions.read(scope, FIELD_SCOPE_TYPE, [scope_id])[scope_id]
    if _current_field(scope, playlist_id, field) == desired:
        return False, revision
    revisions.lock(scope, FIELD_SCOPE_TYPE, scope_id)
    scope.execute(_SET_SQL[field], {"playlist_id": playlist_id, "value": desired})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, scope_id)


def apply_field_operation(scope: LibraryScope, playlist_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_PLAYLIST_FIELD``'s ``apply_field``: SQLite's decision table, result for result."""
    desired = rules.canonical_wire(field, value)
    result: dict[str, Any] = {"playlist_id": playlist_id, "field": field}
    if not lock(scope, playlist_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Playlist lock serializes every writer of its
    # fields, and a refused or converged operation must not create a scope.
    scope_id = rules.scope_key(playlist_id, field)
    revision = revisions.read(scope, FIELD_SCOPE_TYPE, [scope_id])[scope_id]
    current = _current_field(scope, playlist_id, field) or ""
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field(scope, playlist_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


# -- which Playlist a Work is in --------------------------------------------------


def lock_work(scope: LibraryScope, work_id: str) -> bool:
    """Step 1: hold the Work whose Playlist changes; False when it is not this Library's."""
    return links.lock_owner(scope, links.WORK, work_id)


def hold_membership(scope: LibraryScope, work_id: str, desired: str) -> tuple[str, bool]:
    """Step 2 for the Work the caller holds: its Playlist and ``desired``, by id.

    Returns ``(current, desired_exists)``, read after the locks: a Playlist
    delete (which does not hold the Work) can release the Work before its
    Playlist is locked, and then the Work is in none. Every other writer of
    the Work's Playlist holds the Work, so nothing else can move under it.
    """
    before = work_playlist(scope, work_id)
    held = {playlist_id: lock(scope, playlist_id) for playlist_id in sorted({before, desired} - {""})}
    current = work_playlist(scope, work_id) if before else ""
    return current, (not desired or held[desired])


def _move(scope: LibraryScope, work_id: str, current: str, desired: str) -> None:
    """Take the Work out of ``current`` and append it to ``desired``; both Playlists are held."""
    owned = [(ORDER_SCOPE_TYPE, playlist_id) for playlist_id in (current, desired) if playlist_id]
    revisions.lock_many(scope, [*owned, (WORK_SCOPE_TYPE, work_id)])
    scope.execute(_REMOVE_ITEM_SQL, {"work_id": work_id})
    if current:
        revisions.advance(scope, ORDER_SCOPE_TYPE, current)
        _touch(scope, current)
    if desired:
        scope.execute(_APPEND_SQL, {"work_id": work_id, "playlist_id": desired})
        revisions.advance(scope, ORDER_SCOPE_TYPE, desired)
        _touch(scope, desired)
    revisions.advance(scope, WORK_SCOPE_TYPE, work_id)


def set_work_playlist(scope: LibraryScope, work_id: str, playlist_id: Optional[str]) -> bool:
    """``set_work_playlist_on_conn``: add, move or remove (``""``); True when it changed.

    The caller holds the Work (:func:`lock_work`). Run it SERIALIZABLE
    (§7.1: ``playlist_items`` is a peer table of the Work merge).
    """
    desired = ("" if playlist_id is None else str(playlist_id)).strip()
    current, found = hold_membership(scope, work_id, desired)
    if current == desired:
        return False
    if not found:
        raise PlaylistRuleError("PLAYLIST_NOT_FOUND", "Playlist not found.")
    _move(scope, work_id, current, desired)
    return True


def apply_work_playlist_operation(scope: LibraryScope, work_id: str, desired: str,
                                  base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_WORK_PLAYLIST``'s ``apply_work_playlist``: SQLite's decision table, result for result."""
    result: dict[str, Any] = {"work_id": work_id}
    if not lock_work(scope, work_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    current, found = hold_membership(scope, work_id, desired)
    # Read after the Playlists are held: a delete of the Work's Playlist
    # advances this scope, and it waits for them.
    revision = revisions.read(scope, WORK_SCOPE_TYPE, [work_id])[work_id]
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, result
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, result
    changed = current != desired
    if changed and not found:
        result.update(code="PLAYLIST_NOT_FOUND", current_revision=revision)
        return rule_status("PLAYLIST_NOT_FOUND"), result
    if changed:
        _move(scope, work_id, current, desired)
    result.update(code="ACKNOWLEDGED", changed=changed, playlist_id=desired,
                  server_revision=revision + int(changed))
    title = _current_field(scope, desired, "title") if desired else None
    result["playlist_title"] = title or ""
    return 200, result


def file_new_work(scope: LibraryScope, work_id: str, playlist_id: str) -> None:
    """Append a Work this transaction created: construction, no revision moves.

    The caller holds the Playlist (:func:`lock`, before the Work existed).
    """
    scope.execute(_APPEND_SQL, {"work_id": work_id, "playlist_id": playlist_id})
    _touch(scope, playlist_id)


def release_work(scope: LibraryScope, work_id: str) -> None:
    """Take a Work being deleted out of its Playlist (the Work delete's Playlist part).

    The caller holds the Work ``FOR UPDATE``, which every other writer of its
    Playlist waits for, and tombstones ``work-playlist`` with the Work's
    other scopes (``commands.work_scopes``). The Playlist's order changes,
    so its revision advances.
    """
    current, _ = hold_membership(scope, work_id, "")
    if not current:
        return
    revisions.lock(scope, ORDER_SCOPE_TYPE, current)
    scope.execute(_REMOVE_ITEM_SQL, {"work_id": work_id})
    revisions.advance(scope, ORDER_SCOPE_TYPE, current)
    _touch(scope, current)


# -- order ----------------------------------------------------------------------


def set_order(scope: LibraryScope, playlist_id: str, work_ids: list[str]) -> bool:
    """``set_order_on_conn``: rewrite the whole order, keeping membership exactly as it is.

    Raises ``PlaylistRuleError`` (``ENTITY_NOT_FOUND``) for a missing Playlist.
    """
    if not lock(scope, playlist_id):
        raise PlaylistRuleError("ENTITY_NOT_FOUND", "Playlist not found.")
    present = order(scope, playlist_id)
    resolved = rules.resolve_order(present, work_ids)
    if resolved == present:
        return False
    revisions.lock(scope, ORDER_SCOPE_TYPE, playlist_id)
    scope.execute(_REPOSITION_SQL, {"playlist_id": playlist_id, "work_ids": resolved})
    revisions.advance(scope, ORDER_SCOPE_TYPE, playlist_id)
    _touch(scope, playlist_id)
    return True


def apply_order_operation(scope: LibraryScope, playlist_id: str, desired: list[str],
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``REORDER_PLAYLIST_ITEMS``'s ``apply_order``: SQLite's decision table, result for result."""
    result: dict[str, Any] = {"playlist_id": playlist_id}
    if not lock(scope, playlist_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read after the lock, which every writer of the order and of the
    # membership holds: the revision and the members are the committed ones.
    revision = revisions.read(scope, ORDER_SCOPE_TYPE, [playlist_id])[playlist_id]
    present = order(scope, playlist_id)
    resolved = rules.resolve_order(present, desired)
    if base_revision > revision:
        # Counts, never the two orders (playlist_sync).
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(resolved))
        return 400, result
    if base_revision < revision and resolved != present:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(resolved))
        return 409, result
    changed = set_order(scope, playlist_id, desired)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=revision + int(changed))
    final = order(scope, playlist_id)
    if len(final) <= rules.MAX_ECHOED_ITEMS:
        result["work_ids"] = final
    return 200, result


# -- delete ---------------------------------------------------------------------


def delete(scope: LibraryScope, playlist_id: str) -> bool:
    """``delete_playlist_on_conn``: remove a Playlist and release its Works; True when it existed.

    The Playlist is locked ``FOR UPDATE`` first, so every writer of its order
    or membership (each holds it ``FOR NO KEY UPDATE``) has committed and the
    members read after the lock are the committed ones. Each member's
    ``work-playlist`` advances, the order advances, and every field scope
    advances once (see the module docstring).
    """
    if not lock(scope, playlist_id, key=True):
        return False
    members = order(scope, playlist_id)
    fields = [(FIELD_SCOPE_TYPE, rules.scope_key(playlist_id, field)) for field in rules.FIELDS]
    owned = [*fields, (ORDER_SCOPE_TYPE, playlist_id), *((WORK_SCOPE_TYPE, w) for w in members)]
    # A scope no edit touched has no row and reads as revision 0, the base a
    # recreated Playlist starts from: ensured here, it advances with the rest.
    for scope_type, scope_id in sorted(revisions.lock_many(scope, owned)):
        revisions.advance(scope, scope_type, scope_id)
    scope.execute(_REMOVE_ITEMS_SQL, {"playlist_id": playlist_id})
    scope.execute(_DELETE_SQL, {"playlist_id": playlist_id})
    return True


# -- reads ----------------------------------------------------------------------


def catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_all_playlists``: the Playlist columns and ``item_count``."""
    out = []
    for row in scope.execute(_CATALOG_SQL).fetchall():
        item = dict(row)
        item["item_count"] = int(item["item_count"])
        out.append(item)
    return out


def playlist_state(scope: LibraryScope, playlist_id: str) -> Optional[dict[str, Any]]:
    """``get_playlist_state_on_conn``: field revisions and the order revision only."""
    if not exists(scope, playlist_id):
        return None
    fields = sorted(rules.FIELDS)
    by_scope = revisions.read(scope, FIELD_SCOPE_TYPE, [rules.scope_key(playlist_id, f) for f in fields])
    return {"playlist_id": playlist_id,
            "fields": {f: {"revision": by_scope[rules.scope_key(playlist_id, f)]} for f in fields},
            "order_revision": revisions.read(scope, ORDER_SCOPE_TYPE, [playlist_id])[playlist_id]}


def work_playlist_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_playlist_state_on_conn``: which Playlist a Work is in, and that scope's revision."""
    if scope.execute(_WORK_EXISTS_SQL, {"work_id": work_id}).fetchone() is None:
        return None
    return {"work_id": work_id, "playlist_id": work_playlist(scope, work_id),
            "revision": revisions.read(scope, WORK_SCOPE_TYPE, [work_id])[work_id]}
