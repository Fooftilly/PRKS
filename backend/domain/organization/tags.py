"""Tags, their aliases, delete and merge on PostgreSQL (L-O-1 #633).

SQLite's rules (``backend/tag_sync.py``, ``PRKSDatabase``'s tag methods),
with names compared by identity key (``prks.identity_key``, §8.1) instead of
SQLite's ASCII-only ``LOWER``. The name and payload rules are imported from
``tag_sync``.

A Tag's name and every Tag's aliases are one namespace, held by
``tag_keys`` (one row per name or alias, ``UNIQUE (library_id, key)``), as
for publishers. Each write inserts its key row with ``ON CONFLICT DO
NOTHING``: a concurrent writer of the same key makes this one wait until it
commits, and then the conflict names the owner, so a refusal is decided by
the index, never by a read a race could make stale (§7.1). Two creates of
one name therefore make one Tag, an alias and a name can never collide, and
a merge's new alias (the source's name) reuses the key the source's delete
frees in the same transaction.

Tag identity is persistent (``tag_sync``): only :func:`delete_tag` and
:func:`merge` destroy or transform a Tag, and both keep its lifecycle row,
move or remove its links through :func:`links.set_state` (so every link's
scope advances) and report the Works and Folders whose rendered tags change.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, LiteralString, Optional

from backend import tag_sync as rules
from backend.tag_color import normalized_tag_color
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import lifecycle, links
from backend.domain.works import revisions

DEFAULT_COLOR = rules.DEFAULT_COLOR

_OWNER_SQL: LiteralString = """
SELECT k.tag_id, k.alias_id, t.name, t.color
FROM prks.tag_keys k
JOIN prks.tags t ON t.library_id = k.library_id AND t.id = k.tag_id
WHERE k.library_id = %(library_id)s AND k.key = prks.identity_key(%(name)s)
"""
_INSERT_IF_ABSENT_SQL: LiteralString = """
INSERT INTO prks.tags (library_id, id, name, color) VALUES (%(library_id)s, %(tag_id)s, %(name)s, %(color)s)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""
_INSERT_NAME_KEY_SQL: LiteralString = """
INSERT INTO prks.tag_keys (library_id, key, tag_id)
SELECT library_id, name_key, id FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s
ON CONFLICT (library_id, key) DO NOTHING
RETURNING key
"""
_INSERT_ALIAS_SQL: LiteralString = """
INSERT INTO prks.tag_aliases (library_id, id, tag_id, alias)
VALUES (%(library_id)s, %(alias_id)s, %(tag_id)s, %(alias)s)
"""
_INSERT_ALIAS_KEY_SQL: LiteralString = """
INSERT INTO prks.tag_keys (library_id, key, tag_id, alias_id)
SELECT library_id, alias_key, tag_id, id FROM prks.tag_aliases
WHERE library_id = %(library_id)s AND id = %(alias_id)s
ON CONFLICT (library_id, key) DO NOTHING
RETURNING key
"""
_SHARE_SQL: LiteralString = """
SELECT id FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s FOR SHARE
"""
_LOCK_SQL: LiteralString = """
SELECT name FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s FOR NO KEY UPDATE
"""
_LOCK_FOR_CHANGE_SQL: LiteralString = """
SELECT id, name FROM prks.tags WHERE library_id = %(library_id)s AND id = ANY(%(tag_ids)s)
ORDER BY id COLLATE "C" FOR UPDATE
"""
_SET_COLOR_SQL: LiteralString = """
UPDATE prks.tags SET color = %(color)s WHERE library_id = %(library_id)s AND id = %(tag_id)s
"""
_SAME_NAME_SQL: LiteralString = "SELECT prks.identity_key(%(a)s) = prks.identity_key(%(b)s) AS same"
# A Processing draft's tags (``160_processing.sql``) have no revision scope:
# a merge moves them and a delete removes them, as SQLite does its inbox's.
_MOVE_PROCESSING_TAGS_SQL: LiteralString = """
INSERT INTO prks.processing_item_tags (library_id, item_id, tag_id)
SELECT library_id, item_id, %(target)s FROM prks.processing_item_tags
WHERE library_id = %(library_id)s AND tag_id = %(source)s
ON CONFLICT DO NOTHING
"""
_DELETE_PROCESSING_TAGS_SQL: LiteralString = """
DELETE FROM prks.processing_item_tags WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
"""
_DELETE_TAG_ROW_SQL: LiteralString = """
DELETE FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s
"""
_DELETE_ALIAS_ROW_SQL: LiteralString = """
DELETE FROM prks.tag_aliases WHERE library_id = %(library_id)s AND id = %(alias_id)s
"""
_DELETE_ALIAS_SQL: LiteralString = """
DELETE FROM prks.tag_aliases
WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s AND alias_key = prks.identity_key(%(alias)s)
"""
# The key rows follow their aliases (ON UPDATE CASCADE).
_MOVE_ALIASES_SQL: LiteralString = """
UPDATE prks.tag_aliases SET tag_id = %(target)s WHERE library_id = %(library_id)s AND tag_id = %(source)s
"""
_ROW_SQL: LiteralString = """
SELECT id, name, color FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s
"""
_LIFECYCLE_ROW_SQL: LiteralString = """
SELECT state FROM prks.sync_tag_lifecycle WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
"""
# SQLite's ``ORDER BY name`` is BINARY: code points, which COLLATE "C" gives.
_ALL_SQL: LiteralString = """
SELECT t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM prks.tags t WHERE t.library_id = %(library_id)s
ORDER BY t.name COLLATE "C", t.id COLLATE "C"
"""
_IN_USE_SQL: LiteralString = """
SELECT t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
       COALESCE(w.n, 0) AS work_count, COALESCE(f.n, 0) AS folder_count
FROM prks.tags t
LEFT JOIN (SELECT tag_id, count(*) AS n FROM prks.work_tags WHERE library_id = %(library_id)s
           GROUP BY tag_id) w ON w.tag_id = t.id
LEFT JOIN (SELECT tag_id, count(*) AS n FROM prks.folder_tags WHERE library_id = %(library_id)s
           GROUP BY tag_id) f ON f.tag_id = t.id
WHERE t.library_id = %(library_id)s AND (w.n IS NOT NULL OR f.n IS NOT NULL)
ORDER BY t.name COLLATE "C", t.id COLLATE "C"
"""
# SQLite orders by LOWER(), which folds ASCII letters only.
_ALIASES_SQL: LiteralString = """
SELECT tag_id, alias FROM prks.tag_aliases WHERE library_id = %(library_id)s
ORDER BY translate(alias, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         id COLLATE "C"
"""
_RESOLVE_LABEL_SQL: LiteralString = """
SELECT tag_id FROM prks.tag_keys WHERE library_id = %(library_id)s AND key = prks.identity_key(%(label)s)
"""

# How often a create finds the id taken but no Tag of its own Library before
# it concludes the id is another Library's, and how often ``add_tag`` finds
# its name's owner gone (merged or deleted) before it gives up.
_ATTEMPTS = 2

# The keys from a link to its Tag (``backend/domain/organization/__init__.py``).
LINK_KEYS = frozenset({"work_tags_tag_fkey", "folder_tags_tag_fkey", "processing_item_tags_tag_fkey"})

EMPTY_AFFECTED: dict[str, list[str]] = {
    "affected_work_ids": [],
    "affected_folder_ids": [],
    "affected_tag_options_work_ids": [],
    "affected_tag_options_folder_ids": [],
}


class ForeignTagId(Exception):
    """The id is a Tag (or a Tag's lifecycle) of another Library."""


def _clean(value: Optional[str], what: str) -> str:
    text = rules.canonical_name(value)
    if "\x00" in text:
        raise ValueError(what + " cannot contain NUL")
    if len(text.encode("utf-8")) > rules.MAX_NAME_BYTES:
        raise ValueError(what + " is too long")
    return text


def _color(value: Optional[str]) -> str:
    return normalized_tag_color(value, blank_defaults=True)


def _owner(scope: LibraryScope, name: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_OWNER_SQL, {"name": name}).fetchone()
    return None if row is None else dict(row)


def row(scope: LibraryScope, tag_id: str) -> Optional[dict[str, Any]]:
    """``{"id", "name", "color"}``: what the Tag operations acknowledge."""
    found = scope.execute(_ROW_SQL, {"tag_id": tag_id}).fetchone()
    return None if found is None else dict(found)


def share(scope: LibraryScope, tag_id: str) -> bool:
    """Hold the Tag against a delete or a merge until COMMIT; False when absent."""
    return scope.execute(_SHARE_SQL, {"tag_id": tag_id}).fetchone() is not None


# -- creation -------------------------------------------------------------------


def create_if_absent(scope: LibraryScope, tag_id: str, name: str, color: str) -> dict[str, Any]:
    """Insert the Tag with its lifecycle and name key; the outcome as a dict.

    ``{"created": True}`` for a new Tag; ``{"created": False}`` when the id
    is taken (a concurrent create committed it, or another Library has it);
    ``{"name_taken": owner}`` when the name is another Tag's name or alias.
    Raises :class:`ForeignTagId` when another Library has the id's lifecycle.
    """
    if scope.execute(_INSERT_IF_ABSENT_SQL, {"tag_id": tag_id, "name": name, "color": color}).fetchone() is None:
        return {"created": False}
    if not lifecycle.insert_active(scope, tag_id):
        # This Library has no row for the id (the caller looked), so another has.
        raise ForeignTagId(tag_id)
    if scope.execute(_INSERT_NAME_KEY_SQL, {"tag_id": tag_id}).fetchone() is not None:
        return {"created": True}
    # A concurrent writer committed the key first: give the Tag up.
    lifecycle.drop(scope, tag_id)
    scope.execute(_DELETE_TAG_ROW_SQL, {"tag_id": tag_id})
    owner = _owner(scope, name)
    if owner is None:  # pragma: no cover - ON CONFLICT saw a committed key row
        raise RuntimeError("the key row that refused the name is gone")
    return {"name_taken": owner}


def apply_create(scope: LibraryScope, tag_id: str, name: str, color: str) -> tuple[int, dict[str, Any]]:
    """``CREATE_TAG``: SQLite's answers, and an answer for a deleted or merged id.

    An existing Tag of the id is acknowledged as it stands (``changed:
    false``) and held until COMMIT, so a concurrent delete never leaves an
    acknowledgement of a Tag that is gone. A name another Tag holds, as name
    or alias, is ``NAME_TAKEN`` with that Tag. An id whose Tag was deleted
    or merged is ``TAG_DELETED`` or ``TAG_MERGED``: identity is persistent,
    so the id is never constructed again (SQLite fails on the lifecycle
    row's primary key instead). Raises :class:`ForeignTagId` for an id of
    another Library.
    """
    clean = rules.canonical_name(name)
    color = normalized_tag_color(color)
    for _ in range(_ATTEMPTS):
        if share(scope, tag_id):
            return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": False, "tag": row(scope, tag_id)}
        if scope.execute(_LIFECYCLE_ROW_SQL, {"tag_id": tag_id}).fetchone() is not None:
            state = lifecycle.resolve(scope, tag_id)
            if state["state"] == lifecycle.MERGED:
                return 409, {"code": "TAG_MERGED", "tag_id": tag_id, "target_tag_id": state["target_tag_id"]}
            return 409, {"code": "TAG_DELETED", "tag_id": tag_id}
        owner = _owner(scope, clean)
        if owner is not None:
            return 409, {"code": "NAME_TAKEN", "tag_id": tag_id, "target_tag_id": owner["tag_id"]}
        outcome = create_if_absent(scope, tag_id, clean, color)
        if outcome.get("created"):
            return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": True, "tag": row(scope, tag_id)}
        if "name_taken" in outcome:
            return 409, {"code": "NAME_TAKEN", "tag_id": tag_id, "target_tag_id": outcome["name_taken"]["tag_id"]}
        # The id exists: a concurrent create of it committed (and may since
        # have been deleted), or it is another Library's. Look again.
    raise ForeignTagId(tag_id)


def add_tag(scope: LibraryScope, new_id: str, name: Optional[str], color: Optional[str]) -> dict[str, Any]:
    """``add_tag``: ``{"id", "name", "color", "existed"}``; an existing name or alias is its Tag.

    As on SQLite, an existing Tag takes the colour given. The Tag that holds
    the name is locked before its colour changes; if it was merged or
    deleted meanwhile, the name is looked up again.
    """
    raw = _clean(name, "tag name")
    if not raw:
        raise ValueError("tag name is empty")
    paint = _color(color)
    for _ in range(_ATTEMPTS):
        owner = _owner(scope, raw)
        if owner is None:
            outcome = create_if_absent(scope, new_id, raw, paint)
            if outcome.get("created"):
                return {"id": new_id, "name": raw, "color": paint, "existed": False}
            owner = outcome.get("name_taken")
            if owner is None:  # pragma: no cover - a fresh generated id is never taken
                raise RuntimeError("a generated tag id is taken")
        tag_id = str(owner["tag_id"])
        if scope.execute(_LOCK_SQL, {"tag_id": tag_id}).fetchone() is None:
            continue
        scope.execute(_SET_COLOR_SQL, {"tag_id": tag_id, "color": paint})
        found = row(scope, tag_id)
        assert found is not None
        return {**found, "existed": True}
    raise ValueError("tag not found")


# -- aliases --------------------------------------------------------------------


def add_alias(scope: LibraryScope, alias_id: str, tag_id: str, alias: Optional[str]) -> None:
    """``add_tag_alias``: SQLite's refusals, in its order; an alias the Tag already has is a no-op."""
    al = _clean(alias, "alias")
    if not al:
        raise ValueError("alias is empty")
    locked = scope.execute(_LOCK_SQL, {"tag_id": tag_id}).fetchone()
    if locked is None:
        raise ValueError("tag not found")
    same = scope.execute_unscoped(_SAME_NAME_SQL, {"a": str(locked["name"]), "b": al}).fetchone()
    if same is not None and same["same"]:
        raise ValueError("alias matches canonical tag name")
    owner = _owner(scope, al)
    if owner is None:
        params = {"alias_id": alias_id, "tag_id": tag_id, "alias": al}
        scope.execute(_INSERT_ALIAS_SQL, params)
        if scope.execute(_INSERT_ALIAS_KEY_SQL, params).fetchone() is not None:
            return
        scope.execute(_DELETE_ALIAS_ROW_SQL, params)
        owner = _owner(scope, al)
        if owner is None:  # pragma: no cover - ON CONFLICT saw a committed key row
            raise ValueError("alias already used")
    if owner["alias_id"] is None:
        raise ValueError("alias conflicts with another tag name")
    if owner["tag_id"] == tag_id:
        return
    raise ValueError("alias already used")


def delete_alias(scope: LibraryScope, tag_id: str, alias: Optional[str]) -> bool:
    """``delete_tag_alias``: whether an alias of the Tag with that identity key was removed."""
    al = rules.canonical_name(alias)
    if not al or "\x00" in al:
        return False
    return scope.execute(_DELETE_ALIAS_SQL, {"tag_id": tag_id, "alias": al}).rowcount > 0


# -- delete and merge -----------------------------------------------------------


def lock_for_change(scope: LibraryScope, tag_ids: list[str]) -> dict[str, str]:
    """Lock the Tags a delete or merge changes, by id; ``{id: name}`` of those found."""
    rows = scope.execute(_LOCK_FOR_CHANGE_SQL, {"tag_ids": sorted(set(tag_ids))}).fetchall()
    return {str(found["id"]): str(found["name"]) for found in rows}


def _hold_linked(scope: LibraryScope, tag_id: str) -> dict[links.Link, list[str]]:
    """Hold the owners linked to the (locked) Tag, then read the links again.

    No link to the Tag can be added while it is held; one can only vanish,
    with its Work, before the owner is held. So the second read is final.
    """
    linked = {}
    for link in links.LINKS:
        links.share_owners(scope, link, links.owners_of(scope, link, tag_id))
        linked[link] = links.owners_of(scope, link, tag_id)
    return linked


def _affected(scope: LibraryScope, tag_id: str, linked: dict[links.Link, list[str]]) -> dict[str, list[str]]:
    """``entities_linked_to_tag_on_conn``, read before the links change.

    The Works and Folders linked to the Tag (by id; SQLite lists them in
    storage order), and the owners whose tag options name it: those linked
    plus every owner with a revision scope naming it.
    """
    works, folders = linked[links.WORK], linked[links.FOLDER]
    return {
        "affected_work_ids": list(works),
        "affected_folder_ids": list(folders),
        "affected_tag_options_work_ids": sorted(set(works) | links.scoped_owners(scope, links.WORK, tag_id)),
        "affected_tag_options_folder_ids": sorted(set(folders)
                                                  | links.scoped_owners(scope, links.FOLDER, tag_id)),
    }


def delete_tag(scope: LibraryScope, tag_id: str) -> tuple[bool, dict[str, list[str]]]:
    """``delete_tag_on_conn``: remove the Tag, its links (advancing them), aliases and keys.

    Processing drafts lose the Tag too. Returns ``(deleted, affected)``;
    ``deleted`` is False when this Library has no such Tag. The lifecycle
    row stays, ``deleted``.
    """
    if not lock_for_change(scope, [tag_id]):
        return False, {key: list(value) for key, value in EMPTY_AFFECTED.items()}
    linked = _hold_linked(scope, tag_id)
    affected = _affected(scope, tag_id, linked)
    revisions.lock_many(scope, [(link.scope_type, link.scope_id(owner, tag_id))
                                for link, owners in linked.items() for owner in owners])
    for link, owners in linked.items():
        for owner in owners:
            links.set_state(scope, link, owner, tag_id, False)
    scope.execute(_DELETE_PROCESSING_TAGS_SQL, {"tag_id": tag_id})
    lifecycle.mark_deleted(scope, tag_id)
    scope.execute(_DELETE_TAG_ROW_SQL, {"tag_id": tag_id})
    return True, affected


def merge(scope: LibraryScope, source: str, target: str, new_alias_id: str) -> dict[str, Any]:
    """``merge_tags_into_on_conn``: rewrite ``source`` into ``target``.

    The source's links move to the target (both scopes advance; a link the
    target already has only loses the source), so do Processing drafts'
    tags (no scope), the source's lifecycle and
    every lifecycle merged into it point at the target, its aliases move,
    the source is deleted, and its name becomes an alias of the target. The
    shared namespace guarantees the moved aliases and the new one collide
    with nothing, so SQLite's de-duplication has nothing left to drop.
    ``ValueError`` with SQLite's messages for missing ids, a merge into
    itself and a Tag that is not there.
    """
    source = rules.canonical_name(source)
    target = rules.canonical_name(target)
    if not source or not target:
        raise ValueError("source_tag_id and target_tag_id are required")
    if source == target:
        raise ValueError("cannot merge a tag into itself")
    names = lock_for_change(scope, [source, target])
    if source not in names or target not in names:
        raise ValueError("tag not found")
    linked = _hold_linked(scope, source)
    affected = _affected(scope, source, linked)
    moves: dict[links.Link, list[str]] = {
        link: [owner for owner in owners if not links.is_linked(scope, link, owner, target)]
        for link, owners in linked.items()
    }
    revisions.lock_many(scope, [(link.scope_type, link.scope_id(owner, tag))
                                for link, owners in linked.items() for owner in owners
                                for tag in ((source, target) if owner in moves[link] else (source,))])
    for link, owners in linked.items():
        for owner in owners:
            links.set_state(scope, link, owner, target, True)
            links.set_state(scope, link, owner, source, False)
    scope.execute(_MOVE_PROCESSING_TAGS_SQL, {"source": source, "target": target})
    scope.execute(_DELETE_PROCESSING_TAGS_SQL, {"tag_id": source})
    lifecycle.mark_merged(scope, source, target)
    scope.execute(_MOVE_ALIASES_SQL, {"source": source, "target": target})
    # The delete frees the source's name key for the alias below.
    scope.execute(_DELETE_TAG_ROW_SQL, {"tag_id": source})
    params = {"alias_id": new_alias_id, "tag_id": target, "alias": names[source]}
    scope.execute(_INSERT_ALIAS_SQL, params)
    if scope.execute(_INSERT_ALIAS_KEY_SQL, params).fetchone() is None:  # pragma: no cover - key just freed
        scope.execute(_DELETE_ALIAS_ROW_SQL, params)
    return {"canonical_tag_id": target, "canonical_name": names[target], **affected}


# -- reads ----------------------------------------------------------------------


def resolve_label(scope: LibraryScope, label: Optional[str]) -> Optional[str]:
    """``resolve_tag_id_by_label``: the Tag whose name or alias has the label's identity key."""
    name = rules.canonical_name(label)
    if not name or "\x00" in name:
        return None
    found = scope.execute(_RESOLVE_LABEL_SQL, {"label": name}).fetchone()
    return None if found is None else str(found["tag_id"])


def _with_aliases(scope: LibraryScope, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases: dict[str, list[str]] = defaultdict(list)
    if rows:
        for alias_row in scope.execute(_ALIASES_SQL).fetchall():
            aliases[str(alias_row["tag_id"])].append(str(alias_row["alias"]))
    for tag in rows:
        tag["aliases"] = list(aliases.get(str(tag["id"]), []))
    return rows


def all_tags(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_all_tags``: every Tag with its aliases, by name."""
    return _with_aliases(scope, [dict(found) for found in scope.execute(_ALL_SQL).fetchall()])


def tags_in_use(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_tags_in_use``: the Tags linked to a Work or a Folder, with counts and aliases."""
    rows = [dict(found) for found in scope.execute(_IN_USE_SQL).fetchall()]
    for found in rows:
        found["work_count"] = int(found["work_count"])
        found["folder_count"] = int(found["folder_count"])
    return _with_aliases(scope, rows)
