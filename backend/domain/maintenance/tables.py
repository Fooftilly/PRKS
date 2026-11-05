"""Which tables a Library backup carries, and how their columns travel (L-B-1 #639; §11.4).

Every table of the ``prks`` and ``derived`` schemas that has a
``library_id`` column is *library-scoped*, and each one is on exactly one of
three lists (a schema test fails otherwise, so a new table can neither join
nor miss backups silently):

- :data:`BACKUP_CONTENT_TABLES`: the Library's content. Export copies these
  rows, staging builds them, and R4 deletes the Library's rows and inserts
  the staged ones. The list is in dependency order: a parent comes before
  every table that refers to it through a foreign key that cannot be
  deferred, so the R4 inserts, in this order, never wait for a later table.
  The sync ledger, the revisions and the cleanup claims travel with the data
  they describe (§7.3, §11.2).
- :data:`BACKUP_EXCLUDED_TABLES`: access, identity and operational rows that
  belong to the target instance. A backup never exports them and a restore
  never deletes, replaces or writes them (``restore_runs``,
  ``restore_run_objects`` and ``backup_runs`` are written only by the runs'
  own commands).
- :data:`BACKUP_DERIVED_TABLES`: projections. They are never exported; R4
  deletes the Library's rows and enqueues ``derived.rebuild``.

``restore_run_objects`` has no ``library_id`` (it belongs to its run), but is
named on the excluded list too, so the list says everything restore leaves
alone.

**Interchange.** Export writes each content table's stored columns, never
the generated ones (``*_key``, which restore recomputes under the instance's
own identity rule), as text CSV. Each column is recorded in the manifest with
an *interchange type* from :data:`INTERCHANGE_TYPES`, chosen by its
PostgreSQL type, so restore can cast a value into whatever type the current
schema uses. A content column of a type that has no interchange type is a
schema error (:class:`UnsupportedColumnType`), never a silent text copy.

**Foreign keys.** ``CREATE TABLE … (LIKE …)`` does not copy foreign keys, so
staging checks them itself, with each parent redirected to where it will be
after R4 (:func:`parent_route`). A content table's foreign key to a parent
that no rule routes is a schema error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, LiteralString, Protocol

# Bump when the lists or the interchange vocabulary change in a way an older
# PRKS could not read (§11.4, "Changing a column's interchange type is an
# archive format change").
FORMAT_VERSION = 2

# The Library's content, parents first (see the module docstring).
BACKUP_CONTENT_TABLES: tuple[str, ...] = (
    # Works (100_works.sql). works, manifestations and assets refer to each
    # other through deferred keys only.
    "prks.works",
    "prks.manifestations",
    "prks.assets",
    "prks.manifestation_relations",
    "prks.manifestation_identifiers",
    "prks.work_notes",
    "prks.sync_work_lifecycle",
    "prks.duplicate_decisions",
    # People (110_people.sql).
    "prks.persons",
    "prks.person_aliases",
    "prks.roles",
    "prks.person_groups",
    "prks.person_group_members",
    "prks.publishers",
    "prks.publisher_aliases",
    "prks.publisher_keys",
    # Organization (120_organization.sql).
    "prks.sync_tag_lifecycle",
    "prks.tags",
    "prks.tag_aliases",
    "prks.tag_keys",
    "prks.work_tags",
    "prks.folders",
    "prks.folder_files",
    "prks.folder_tags",
    "prks.playlists",
    "prks.playlist_items",
    "prks.saved_views",
    "prks.library_settings",
    # Research (130_research.sql).
    "prks.concepts",
    "prks.concept_aliases",
    "prks.concept_keys",
    "prks.concept_parents",
    "prks.positions",
    "prks.arguments",
    "prks.argument_target_positions",
    "prks.argument_target_arguments",
    "prks.argument_sources",
    # Annotations (140_annotations.sql).
    "prks.annotations",
    # Processing queue (160_processing.sql, §11.5): ordinary content rows.
    "prks.processing_items",
    "prks.processing_item_drafts",
    "prks.processing_item_roles",
    "prks.processing_item_tags",
    # Cleanup claims travel with the objects they describe (150_objects.sql).
    "prks.object_cleanup_claims",
    # Revisions and the sync ledger (040_sync.sql, §7.3).
    "prks.entity_revisions",
    "prks.sync_operations",
)

# Never exported, never touched by a restore (§11.4, "Not exported").
BACKUP_EXCLUDED_TABLES: frozenset[str] = frozenset({
    "prks.library_memberships",
    "prks.sessions",
    "prks.jobs",
    "prks.backup_runs",
    "prks.restore_runs",
    "prks.restore_run_objects",
})

# Deleted by R4 and rebuilt by ``derived.rebuild`` (§8.6, §11.4).
BACKUP_DERIVED_TABLES: frozenset[str] = frozenset({
    "derived.work_note_state",
    "derived.concept_mentions",
    "derived.argument_mentions",
    "derived.work_search",
    "derived.asset_text",
})

# Identity parents a content row may refer to. Users and devices travel as
# credential-free identities and are mapped on restore; the Library is
# mapped to the target Library.
IDENTITY_TABLES: frozenset[str] = frozenset({"prks.libraries", "prks.users", "prks.devices"})

# Global seed and reference rows that no backup carries; a content row's key
# into one is checked against the live table.
GLOBAL_REFERENCE_TABLES: frozenset[str] = frozenset({"prks.argument_verdicts"})

# PostgreSQL type (``format_type``) → interchange type (§11.4).
INTERCHANGE_TYPES: Mapping[str, str] = {
    "text": "text",
    "bigint": "int64",
    "integer": "int64",
    "smallint": "int64",
    "boolean": "bool",
    "timestamp with time zone": "timestamptz",
    "jsonb": "json",
    "uuid": "uuid",
    "bytea": "bytes",
}


class UnsupportedColumnType(ValueError):
    """A content column whose PostgreSQL type has no interchange type."""


class UnroutedForeignKey(ValueError):
    """A content table's foreign key to a parent that no staging rule routes."""


class ParentRoute(Enum):
    """Where staging checks a foreign key's parent (§11.4, "Staging schema")."""

    # A content table: R4 replaces the live rows, so only the staged copy counts.
    STAGED = "staged"
    # ``prks.devices``: the live rows plus the devices the mapping will insert in R4.
    DEVICES = "devices"
    # ``prks.libraries`` and ``prks.users``: the live rows, after identity mapping.
    IDENTITY = "identity"
    # A global reference table: the live rows.
    GLOBAL = "global"


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str
    interchange: str


@dataclass(frozen=True)
class ForeignKey:
    """One foreign key of a content table, as ``pg_constraint`` records it."""

    name: str
    table: str
    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    deferrable: bool

    @property
    def route(self) -> ParentRoute:
        return parent_route(self.parent)


class _Cursor(Protocol):
    def fetchall(self) -> Sequence[Any]: ...


class _Executor(Protocol):
    """A psycopg connection or :class:`backend.db.database.Transaction`."""

    def execute(self, query: LiteralString, params: Any = None) -> _Cursor: ...


def parent_route(parent: str) -> ParentRoute:
    """The staging rule for a foreign key into ``parent`` (a qualified table name)."""
    if parent in BACKUP_CONTENT_TABLES:
        return ParentRoute.STAGED
    if parent == "prks.devices":
        return ParentRoute.DEVICES
    if parent in IDENTITY_TABLES:
        return ParentRoute.IDENTITY
    if parent in GLOBAL_REFERENCE_TABLES:
        return ParentRoute.GLOBAL
    raise UnroutedForeignKey(f"no staging rule routes a foreign key into {parent}")


def split_name(table: str) -> tuple[str, str]:
    """``("prks", "works")`` for ``"prks.works"``; refuses anything not on a list."""
    if table not in BACKUP_CONTENT_TABLES and table not in BACKUP_EXCLUDED_TABLES \
            and table not in BACKUP_DERIVED_TABLES:
        raise ValueError(f"{table} is not a classified table")
    schema, _, name = table.partition(".")
    return schema, name


def interchange_type(pg_type: str) -> str:
    try:
        return INTERCHANGE_TYPES[pg_type]
    except KeyError:
        raise UnsupportedColumnType(f"no interchange type for PostgreSQL type {pg_type!r}") from None


_COLUMNS_SQL: LiteralString = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod)
  FROM pg_attribute a
 WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
 ORDER BY a.attnum
"""


def exported_columns(conn: _Executor, table: str) -> list[Column]:
    """The stored (non-generated) columns of a content table, in column order."""
    if table not in BACKUP_CONTENT_TABLES:
        raise ValueError(f"{table} is not a content table")
    rows = conn.execute(_COLUMNS_SQL, (table,)).fetchall()
    if not rows:
        raise ValueError(f"{table} does not exist")
    return [Column(name=row[0], pg_type=row[1], interchange=interchange_type(row[1])) for row in rows]


def interchange_contract(conn: _Executor) -> dict[str, list[dict[str, str]]]:
    """The manifest's ``interchange`` block: every content table's exported columns and types."""
    return {
        table: [{"column": c.name, "type": c.interchange} for c in exported_columns(conn, table)]
        for table in BACKUP_CONTENT_TABLES
    }


_LIBRARY_SCOPED_SQL: LiteralString = """
SELECT n.nspname || '.' || c.relname
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'library_id' AND NOT a.attisdropped
 WHERE n.nspname IN ('prks', 'derived') AND c.relkind IN ('r', 'p')
 ORDER BY 1
"""


def library_scoped_tables(conn: _Executor) -> list[str]:
    """Every ``prks``/``derived`` table that has a ``library_id`` column."""
    return [row[0] for row in conn.execute(_LIBRARY_SCOPED_SQL).fetchall()]


_FOREIGN_KEYS_SQL: LiteralString = """
SELECT k.conname,
       array(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i)
               JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n ORDER BY u.i),
       pn.nspname || '.' || pc.relname,
       array(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i)
               JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n ORDER BY u.i),
       k.condeferrable
  FROM pg_constraint k
  JOIN pg_class pc ON pc.oid = k.confrelid
  JOIN pg_namespace pn ON pn.oid = pc.relnamespace
 WHERE k.contype = 'f' AND k.conrelid = to_regclass(%s)
 ORDER BY k.conname
"""


def foreign_keys(conn: _Executor, table: str) -> list[ForeignKey]:
    """The foreign keys of a content table, with their parents qualified."""
    if table not in BACKUP_CONTENT_TABLES:
        raise ValueError(f"{table} is not a content table")
    return [
        ForeignKey(name=row[0], table=table, columns=tuple(row[1]), parent=row[2],
                   parent_columns=tuple(row[3]), deferrable=bool(row[4]))
        for row in conn.execute(_FOREIGN_KEYS_SQL, (table,)).fetchall()
    ]


def identity_columns(conn: _Executor) -> dict[str, list[ForeignKey]]:
    """The content-table keys into ``prks.users`` and ``prks.devices``, by parent.

    These name the attribution identities a backup exports (§11.4,
    "Attribution identities, without credentials").
    """
    found: dict[str, list[ForeignKey]] = {"prks.users": [], "prks.devices": []}
    for table in BACKUP_CONTENT_TABLES:
        for fk in foreign_keys(conn, table):
            if fk.parent in found:
                found[fk.parent].append(fk)
    return found
