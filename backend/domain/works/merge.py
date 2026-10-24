"""Work merge foundations on PostgreSQL (L-W-2 #631).

``MERGE_WORKS(source → target)`` (``docs/work-identity-model.md`` §10.4) is
not available yet: no facade method, sync operation or route reaches it. This
module holds what the command will be built on, and nothing that writes a
merge:

- :data:`RELATIONSHIPS`: every table that holds a row of a Work, a
  Manifestation or an Asset, what a merge does with it, and whether deleting
  the source would cascade it away (so the merge must move it first) or
  refuse. A schema test compares it with the live foreign keys, so a lane
  that adds such a table fails until it says what a merge does with it.
- :data:`UNKEYED`: the tables that name a Work by id without a foreign key,
  so no schema test can find them, and what a merge does with each.
- :data:`DEFERRED`: the tables the merge must handle that do not exist on
  PostgreSQL yet, with the slice that brings each. The same test fails when
  one appears, so the merge cannot become available while one is unhandled.
- :func:`prepare`: the merge's first half, in the merge's own SERIALIZABLE
  transaction. It validates the pair, locks both Works, and reads every
  affected row and revision scope into a :class:`MergePlan`: the preview the
  user confirms, and the input of the writes that follow. It writes nothing.
- :data:`PEER_WRITERS`: the operations that add rows to a peer table the
  merge reads as a set (``docs/postgresql-architecture.md`` §7.1). Each must
  run SERIALIZABLE, or a row could join the source after ``prepare`` read it
  and be lost when the source is deleted (``work_tags`` and Work-scoped
  ``roles`` cascade).

**Roles.** ``source`` is merged away: its id gets ``sync_work_lifecycle(merged,
target)`` (:func:`backend.domain.works.lifecycle.mark_merged`), every pending
operation naming it is refused ``WORK_MERGED`` (the sync gate,
:func:`~backend.domain.works.lifecycle.guard`), and its revision scopes are
tombstoned. ``target`` survives with its id, its primary and citation
Manifestations, and its revisions, which advance for what the merge changes.

**Transaction.** :func:`prepare` refuses to run outside SERIALIZABLE. It locks
both Works ``FOR UPDATE`` in id order (``COLLATE "C"``), the first rows it
takes, so two merges of overlapping Works queue rather than deadlock, and
every writer of either Work, which holds the Work row (``FOR KEY SHARE`` at
least, which every foreign-key insert takes), conflicts with it: it finished
before (and this transaction fails ``40001`` and is retried by
``library_command``) or waits for the merge. A peer row that joins without
touching the Work row is caught by SSI, because every peer writer is
SERIALIZABLE (:data:`PEER_WRITERS`).

**What is deferred, and to whom.** The writes of §10.4 steps 1-6; the
preview API and the user's choices (#185 pattern); the ``MERGE_WORKS``
durable operation; reads that follow the redirect; and the tables in
:data:`DEFERRED`. A credit collision (§10.4 step 4) needs
``manifestation_credit_overrides`` (#60 Slice K), so a plan that has one is
:attr:`MergePlan.blocked`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import commands, lifecycle


class Disposition(enum.Enum):
    """What a merge does with a row of the source."""

    MOVED = "re-pointed to the target by the merge (UPDATE … SET work_id)"
    FOLLOWS = "follows its Manifestation or Asset by ON UPDATE CASCADE"
    UNION = "unioned into the target; identical rows collapse"
    TARGET_WINS = "the target's is kept unless the user picks the source's"
    USER_CHOICE = "the user chooses: keep the target's, the source's, or both with a separator"
    RELEASED = "the source's pointer is released before its Manifestations move; the target's stays"


class OnSourceDelete(enum.Enum):
    """What deleting the source Work (or a Manifestation) does to a row left behind."""

    CASCADE = "deleted with it: the merge must move the row first or lose it"
    REFUSED = "the delete fails: a forgotten row aborts the merge"
    NOT_REACHED = "not reached: the row's parent is moved, never deleted"


@dataclass(frozen=True)
class Relationship:
    table: str
    constraint: str
    references: str  # the referenced table in prks: works, manifestations or assets
    disposition: Disposition
    on_source_delete: OnSourceDelete
    owner: str
    peer: bool  # read as a set by the merge (§7.1 peer table)


RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("works", "works_primary_manifestation_fkey", "manifestations", Disposition.RELEASED,
                 OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("works", "works_citation_manifestation_fkey", "manifestations", Disposition.RELEASED,
                 OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("manifestations", "manifestations_work_fkey", "works", Disposition.MOVED,
                 OnSourceDelete.REFUSED, "L-W-1", peer=True),
    Relationship("manifestations", "manifestations_primary_asset_fkey", "assets", Disposition.FOLLOWS,
                 OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("assets", "assets_manifestation_fkey", "manifestations", Disposition.FOLLOWS,
                 OnSourceDelete.NOT_REACHED, "L-W-1 (catalogue), L-A-1 (objects)", peer=False),
    Relationship("assets", "assets_derived_from_fkey", "assets", Disposition.FOLLOWS,
                 OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("assets", "assets_supersedes_fkey", "assets", Disposition.FOLLOWS,
                 OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("manifestation_relations", "manifestation_relations_from_fkey", "manifestations",
                 Disposition.FOLLOWS, OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("manifestation_relations", "manifestation_relations_to_fkey", "manifestations",
                 Disposition.FOLLOWS, OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("manifestation_identifiers", "manifestation_identifiers_manifestation_fkey",
                 "manifestations", Disposition.FOLLOWS, OnSourceDelete.NOT_REACHED, "L-W-1", peer=False),
    Relationship("work_notes", "work_notes_work_fkey", "works", Disposition.USER_CHOICE,
                 OnSourceDelete.REFUSED, "L-W-1", peer=True),
    Relationship("roles", "roles_work_fkey", "works", Disposition.UNION,
                 OnSourceDelete.CASCADE, "L-P-1", peer=True),
    Relationship("roles", "roles_manifestation_fkey", "manifestations", Disposition.FOLLOWS,
                 OnSourceDelete.NOT_REACHED, "L-P-1", peer=True),
    Relationship("work_tags", "work_tags_work_fkey", "works", Disposition.UNION,
                 OnSourceDelete.CASCADE, "L-O-1", peer=True),
    Relationship("folder_files", "folder_files_work_fkey", "works", Disposition.TARGET_WINS,
                 OnSourceDelete.REFUSED, "L-O-1", peer=True),
    Relationship("playlist_items", "playlist_items_work_fkey", "works", Disposition.TARGET_WINS,
                 OnSourceDelete.REFUSED, "L-O-1", peer=True),
)


@dataclass(frozen=True)
class Deferred:
    table: str
    owner: str
    what_the_merge_does: str


DEFERRED: tuple[Deferred, ...] = (
    Deferred("argument_sources", "L-R-1 (#634)",
             "re-points Work-level citations; pinned ones follow their Manifestation; only exactly "
             "identical citations collapse, and every changed Argument's argument-sources revision "
             "advances (§8.5)"),
    Deferred("processing_files", "L-PR-1",
             "re-points imported_work_id, which ON DELETE SET NULL would otherwise drop"),
    Deferred("annotations", "L-A-1",
             "nothing: an annotation follows its Asset"),
    Deferred("manifestation_credit_overrides", "#60 Slice K (after 0.8)",
             "freezes the source's spelling of a colliding Work-scoped credit on each moved Version "
             "(§10.4 step 4)"),
)

@dataclass(frozen=True)
class Unkeyed:
    table: str
    columns: str
    owner: str
    what_the_merge_does: str


UNKEYED: tuple[Unkeyed, ...] = (
    Unkeyed("duplicate_decisions", "low_id, high_id (entity_type 'work')", "Works (#60)",
            "revisits every declined pair naming the source, which is merged away (§10.5); "
            "Manifestation and Asset ids survive a Work merge, so only Work pairs are read"),
)

# Operations that add rows to a peer table of the Work merge, or move a row
# onto a Work (§7.1). Each must be SERIALIZABLE.
PEER_WRITERS: frozenset[str] = frozenset({
    "CREATE_WORK",              # manifestations, work_notes
    "SET_WORK_RESEARCH_NOTE",   # work_notes
    "SET_WORK_PRIVATE_NOTE",    # work_notes
    "ADD_WORK_TAG",             # work_tags
    "MERGE_TAG",                # work_tags
    "SET_WORK_FOLDER",          # folder_files
    "SET_WORK_PLAYLIST",        # playlist_items
    "ADD_WORK_PERSON_ROLE",     # roles
})


class MergeRefused(Exception):
    """The terminal refusal of a merge request: ``status`` and ``result``, as a family returns them."""

    def __init__(self, status: int, result: dict[str, Any]) -> None:
        super().__init__(result["code"])
        self.status = status
        self.result = result


@dataclass(frozen=True)
class Side:
    """One value of each Work, when the two differ."""

    source: Any
    target: Any

    @property
    def differs(self) -> bool:
        return self.source != self.target


@dataclass(frozen=True)
class RoleCollision:
    person_id: str
    role_type: str
    source_credit: Optional[str]
    target_credit: Optional[str]


@dataclass(frozen=True)
class MergePlan:
    """Everything a merge of ``source_id`` into ``target_id`` would change, read in one snapshot."""

    source_id: str
    target_id: str
    # Moved to the target (§10.4 step 3), with their Assets and Manifestation-scoped roles.
    manifestation_ids: tuple[str, ...]
    # (Manifestation, field, value): inherited values written as overrides before the move.
    frozen: tuple[tuple[str, str, str], ...]
    # (Manifestation, field): the source's value is empty, so the Version will show the target's.
    shows_target: tuple[tuple[str, str], ...]
    asset_count: int
    manifestation_role_count: int
    tags_added: tuple[str, ...]
    tags_collapsed: tuple[str, ...]
    roles_moved: tuple[tuple[str, str], ...]       # (person, role_type)
    roles_collapsed: tuple[tuple[str, str], ...]
    role_collisions: tuple[RoleCollision, ...]
    folder: Side
    playlist: Side
    notes: dict[str, Side]                          # "research" / "private" -> bodies ("" when absent)
    status: Side
    # Work-owned free text (§5), and BibTeX's author when no Author is linked
    # (citation.py), so it never disappears with the source: the source's
    # value is kept when the target has none ("" when absent).
    author_text: Side
    last_opened_at: Any                             # the max of the two
    source_scopes: tuple[tuple[str, str], ...]      # tombstoned with the source
    # (low_id, high_id): declined Work pairs naming the source (UNKEYED, §10.5).
    duplicate_decisions: tuple[tuple[str, str], ...]
    blocked: tuple[str, ...] = field(default=())

    @property
    def choices(self) -> tuple[str, ...]:
        """The decisions the user makes in the preview: values on which the two Works differ."""
        named: list[str] = []
        if self.folder.differs and self.folder.source is not None and self.folder.target is not None:
            named.append("folder")
        if self.playlist.differs and self.playlist.source is not None and self.playlist.target is not None:
            named.append("playlist")
        named.extend("note:" + kind for kind, side in sorted(self.notes.items())
                     if side.differs and side.source and side.target)
        if self.status.differs:
            named.append("status")
        if self.author_text.differs and self.author_text.source and self.author_text.target:
            named.append("author_text")
        return tuple(named)


_ISOLATION: LiteralString = "SELECT current_setting('transaction_isolation') AS level"
# Every Work-owned column but hide_pdf_link_annotations, a viewer
# preference on which the target wins without a choice (§10.4).
_LOCK_PAIR: LiteralString = """
SELECT id, title, abstract, status, author_text, last_opened_at FROM prks.works
WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[])
ORDER BY id COLLATE "C"
FOR UPDATE
"""
_MANIFESTATIONS: LiteralString = """
SELECT id, title, abstract FROM prks.manifestations
WHERE library_id = %(library_id)s AND work_id = %(work_id)s
ORDER BY id COLLATE "C"
"""
_COUNTS: LiteralString = """
SELECT (SELECT count(*) FROM prks.assets WHERE library_id = %(library_id)s AND work_id = %(work_id)s) AS assets,
       (SELECT count(*) FROM prks.roles WHERE library_id = %(library_id)s AND work_id = %(work_id)s
          AND manifestation_id IS NOT NULL) AS manifestation_roles
"""
_TAGS: LiteralString = """
SELECT tag_id, work_id = %(target)s AS on_target FROM prks.work_tags
WHERE library_id = %(library_id)s AND work_id = ANY(ARRAY[%(source)s, %(target)s])
ORDER BY tag_id COLLATE "C"
"""
_WORK_ROLES: LiteralString = """
SELECT person_id, role_type, credit_name, work_id = %(target)s AS on_target FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = ANY(ARRAY[%(source)s, %(target)s])
  AND manifestation_id IS NULL
ORDER BY person_id COLLATE "C", role_type COLLATE "C"
"""
_FOLDERS: LiteralString = """
SELECT work_id, folder_id FROM prks.folder_files
WHERE library_id = %(library_id)s AND work_id = ANY(ARRAY[%(source)s, %(target)s])
"""
_PLAYLISTS: LiteralString = """
SELECT work_id, playlist_id FROM prks.playlist_items
WHERE library_id = %(library_id)s AND work_id = ANY(ARRAY[%(source)s, %(target)s])
"""
_NOTES: LiteralString = """
SELECT work_id, kind, body FROM prks.work_notes
WHERE library_id = %(library_id)s AND work_id = ANY(ARRAY[%(source)s, %(target)s])
"""
_DECISIONS: LiteralString = """
SELECT low_id, high_id FROM prks.duplicate_decisions
WHERE library_id = %(library_id)s AND entity_type = 'work'
  AND (low_id = %(work_id)s OR high_id = %(work_id)s)
ORDER BY low_id COLLATE "C", high_id COLLATE "C"
"""
# The tombstone_work_scopes predicate, read: every scope row the source owns.
_SOURCE_SCOPES: LiteralString = """
SELECT scope_type, scope_id FROM prks.entity_revisions
WHERE library_id = %(library_id)s
  AND (scope_id = %(work_id)s
       OR scope_id = %(array_scope)s
       OR left(scope_id, %(prefix_len)s) = %(array_prefix)s)
"""


def prepare(scope: LibraryScope, source_id: str, target_id: str) -> MergePlan:
    """Validate and lock the pair and read the merge's plan; writes nothing.

    Raises :class:`MergeRefused`: ``MERGE_SAME_WORK`` (400) for a Work merged
    into itself; ``WORK_MERGED`` (409, with ``target_work_id``) when either
    id was merged away; ``ENTITY_NOT_FOUND`` (404, ``work_id``) when either is
    not a Work of this Library, which is all an id of another Library ever
    reads as. Raises ``RuntimeError`` outside a SERIALIZABLE transaction.
    """
    level = scope.execute_unscoped(_ISOLATION).fetchone()
    if level is None or level["level"] != "serializable":
        raise RuntimeError("a Work merge runs SERIALIZABLE (docs/postgresql-architecture.md §7.1)")
    if source_id == target_id:
        raise MergeRefused(400, {"code": "MERGE_SAME_WORK", "work_id": source_id})
    rows = {str(row["id"]): row for row in scope.execute(_LOCK_PAIR, {"ids": [source_id, target_id]})}
    for work_id in (source_id, target_id):
        if work_id not in rows:
            state = lifecycle.resolve(scope, work_id)
            if state["state"] == lifecycle.MERGED:
                status, result = lifecycle.refusal(work_id, state["target_work_id"])
                raise MergeRefused(status, result)
            raise MergeRefused(404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id})
    source, target = rows[source_id], rows[target_id]
    pair = {"source": source_id, "target": target_id}

    manifestations = scope.execute(_MANIFESTATIONS, {"work_id": source_id}).fetchall()
    frozen: list[tuple[str, str, str]] = []
    shows_target: list[tuple[str, str]] = []
    for row in manifestations:
        for name in ("title", "abstract"):
            if row[name] is not None:
                continue  # an override of its own stays
            inherited, after = source[name] or "", target[name] or ""
            if inherited == after:
                continue
            if inherited:
                frozen.append((str(row["id"]), name, inherited))
            else:
                shows_target.append((str(row["id"]), name))
    counts = scope.execute(_COUNTS, {"work_id": source_id}).fetchone()
    assert counts is not None

    tags: dict[str, set[bool]] = {}
    for row in scope.execute(_TAGS, pair):
        tags.setdefault(str(row["tag_id"]), set()).add(bool(row["on_target"]))
    roles: dict[tuple[str, str], dict[bool, Optional[str]]] = {}
    for row in scope.execute(_WORK_ROLES, pair):
        key = (str(row["person_id"]), str(row["role_type"]))
        roles.setdefault(key, {})[bool(row["on_target"])] = row["credit_name"]
    moved = tuple(key for key, sides in roles.items() if set(sides) == {False})
    collapsed = tuple(key for key, sides in roles.items()
                      if set(sides) == {False, True} and sides[False] == sides[True])
    collisions = tuple(RoleCollision(person, role, sides[False], sides[True])
                       for (person, role), sides in roles.items()
                       if set(sides) == {False, True} and sides[False] != sides[True])

    def sides(sql: LiteralString, column: str) -> Side:
        found = {str(row["work_id"]): row[column] for row in scope.execute(sql, pair)}
        return Side(found.get(source_id), found.get(target_id))

    notes: dict[str, dict[str, str]] = {"research": {}, "private": {}}
    for row in scope.execute(_NOTES, pair):
        notes[str(row["kind"])][str(row["work_id"])] = str(row["body"])
    opened = [value for value in (source["last_opened_at"], target["last_opened_at"]) if value is not None]
    array_prefix = '["' + source_id + '",'
    scope_rows = scope.execute(_SOURCE_SCOPES, {
        "work_id": source_id, "array_scope": '["' + source_id + '"]',
        "array_prefix": array_prefix, "prefix_len": len(array_prefix),
    }).fetchall()
    source_scopes = sorted({(str(row["scope_type"]), str(row["scope_id"])) for row in scope_rows}
                           | set(commands.work_scopes(source_id)))
    blocked = ("manifestation_credit_overrides (#60 Slice K): a Work-scoped credit differs",) if collisions else ()
    return MergePlan(
        source_id=source_id, target_id=target_id,
        manifestation_ids=tuple(str(row["id"]) for row in manifestations),
        frozen=tuple(frozen), shows_target=tuple(shows_target),
        asset_count=int(counts["assets"]), manifestation_role_count=int(counts["manifestation_roles"]),
        tags_added=tuple(tag for tag, where in tags.items() if where == {False}),
        tags_collapsed=tuple(tag for tag, where in tags.items() if where == {False, True}),
        roles_moved=moved, roles_collapsed=collapsed, role_collisions=collisions,
        folder=sides(_FOLDERS, "folder_id"), playlist=sides(_PLAYLISTS, "playlist_id"),
        notes={kind: Side(by_work.get(source_id, ""), by_work.get(target_id, ""))
               for kind, by_work in notes.items()},
        status=Side(source["status"], target["status"]),
        author_text=Side(source["author_text"] or "", target["author_text"] or ""),
        last_opened_at=max(opened) if opened else None,
        source_scopes=tuple(source_scopes),
        duplicate_decisions=tuple((str(row["low_id"]), str(row["high_id"]))
                                  for row in scope.execute(_DECISIONS, {"work_id": source_id})),
        blocked=blocked,
    )
