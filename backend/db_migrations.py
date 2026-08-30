"""Ordered SQLite schema migrations for PRKS.

Fresh databases are created from db_schema.sql at LATEST_SCHEMA_VERSION.
Existing databases are inspected, then upgraded through an explicit registry.

Versions below LEGACY_BASELINE_VERSION have no reconstructable step history.
They are normalized to the v9 compatibility baseline once, then the ordered
9→10 migration enforces the current schema contract.

Migrations may modify SQLite state only — never managed filesystem data.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backend.log_safety import safe_error_type, safe_log_label

LOGGER = logging.getLogger("prks.db")

LATEST_SCHEMA_VERSION = 10
LEGACY_BASELINE_VERSION = 9

# Unversioned files count as PRKS only with works plus another established table.
# Do not treat an arbitrary SQLite DB as a legacy library.
_LEGACY_MARKER_CORE = "works"
_LEGACY_MARKER_COMPANIONS = frozenset(
    {
        "persons",
        "roles",
        "folders",
        "tags",
        "annotations",
        "work_annotations",
        "arguments",
        "concepts",
        "playlists",
        "app_settings",
        "folder_files",
        "work_tags",
        "works_fts",
    }
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MIGRATION_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_CREATE_TABLE_RE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX_RE = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_TRIGGER_RE = re.compile(
    r"^CREATE\s+TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_VTABLE_RE = re.compile(
    r"^CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)

NEWER_SCHEMA_MESSAGE = (
    "This database was created by a newer PRKS version. Update PRKS before opening it."
)

REQUIRED_TABLES = (
    "schema_version",
    "app_settings",
    "works",
    "playlists",
    "playlist_items",
    "persons",
    "concepts",
    "arguments",
    "roles",
    "annotations",
    "work_annotations",
    "processing_files",
    "processing_file_roles",
    "processing_file_tags",
    "works_fts",
    "folders",
    "folder_files",
    "tags",
    "work_tags",
    "folder_tags",
    "tag_aliases",
    "publishers",
    "publisher_aliases",
    "person_groups",
    "person_group_members",
)

REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "works": (
        "id",
        "title",
        "status",
        "published_date",
        "abstract",
        "text_content",
        "file_path",
        "source_kind",
        "source_url",
        "source_mime",
        "thumb_url",
        "provider",
        "provider_id",
        "urldate",
        "thumb_page",
        "author_text",
        "year",
        "publisher",
        "location",
        "edition",
        "journal",
        "volume",
        "issue",
        "pages",
        "isbn",
        "doi",
        "doc_type",
        "private_notes",
        "hide_pdf_link_annotations",
        "last_opened_at",
        "created_at",
        "updated_at",
    ),
    "persons": (
        "id",
        "first_name",
        "last_name",
        "aliases",
        "about",
        "image_url",
        "link_wikipedia",
        "link_stanford_encyclopedia",
        "link_iep",
        "links_other",
        "birth_date",
        "death_date",
        "created_at",
        "updated_at",
    ),
    "folders": (
        "id",
        "title",
        "description",
        "private_notes",
        "parent_id",
        "created_at",
        "updated_at",
    ),
    "playlists": ("id", "title", "description", "original_url", "created_at", "updated_at"),
    "roles": ("person_id", "work_id", "role_type", "order_index", "credit_name"),
    "processing_files": (
        "id",
        "rel_path",
        "abs_path",
        "filename",
        "status",
        "last_error",
        "imported_work_id",
        "imported_at",
        "discovered_at",
        "updated_at",
        "title",
        "status_draft",
        "published_date",
        "abstract",
        "source_url",
        "author_text",
        "year",
        "publisher",
        "location",
        "edition",
        "journal",
        "volume",
        "issue",
        "pages",
        "isbn",
        "doi",
        "doc_type",
        "private_notes",
        "thumb_page",
        "target_folder_id",
    ),
    "publishers": ("id", "name"),
    "publisher_aliases": ("id", "publisher_id", "alias"),
    "processing_file_tags": ("processing_file_id", "tag_id"),
    "tags": ("id", "name"),
    "person_groups": ("id", "name"),
    "playlist_items": ("playlist_id", "work_id", "position"),
}

@dataclass(frozen=True)
class IndexSpec:
    name: str
    table: str
    unique: bool
    columns: Tuple[str, ...] = ()
    collations: Tuple[str, ...] = ()
    expression_tokens: Tuple[str, ...] = ()


INDEX_SPECS: Tuple[IndexSpec, ...] = (
    IndexSpec(
        "idx_playlist_items_work_unique",
        "playlist_items",
        True,
        columns=("work_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_person_groups_name_nocase",
        "person_groups",
        True,
        columns=("name",),
        collations=("NOCASE",),
    ),
    IndexSpec(
        "idx_tags_name_nocase",
        "tags",
        True,
        columns=("name",),
        collations=("NOCASE",),
    ),
    IndexSpec(
        "idx_publishers_name_nocase",
        "publishers",
        True,
        columns=("name",),
        collations=("NOCASE",),
    ),
    IndexSpec(
        "idx_publisher_aliases_alias_nocase",
        "publisher_aliases",
        True,
        columns=("alias",),
        collations=("NOCASE",),
    ),
    IndexSpec(
        "idx_tag_aliases_alias_nocase",
        "tag_aliases",
        True,
        columns=("alias",),
        collations=("NOCASE",),
    ),
    IndexSpec("idx_roles_work_id", "roles", False, columns=("work_id",), collations=("BINARY",)),
    IndexSpec("idx_roles_person_id", "roles", False, columns=("person_id",), collations=("BINARY",)),
    IndexSpec(
        "idx_annotations_work_id",
        "annotations",
        False,
        columns=("work_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_arguments_work_id",
        "arguments",
        False,
        columns=("work_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_playlist_items_playlist_id",
        "playlist_items",
        False,
        columns=("playlist_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_works_last_opened_at",
        "works",
        False,
        columns=("last_opened_at",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_folders_parent_id",
        "folders",
        False,
        columns=("parent_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_processing_files_status",
        "processing_files",
        False,
        columns=("status",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_folders_parent_title_nocase",
        "folders",
        True,
        expression_tokens=("COALESCE(parent_id, '')", "LOWER(TRIM(title))"),
    ),
)

REQUIRED_INDEXES = tuple(spec.name for spec in INDEX_SPECS)

REQUIRED_FTS_COLUMNS = ("title", "abstract", "text_content", "author_text")
REQUIRED_FTS_TRIGGERS = ("works_ai", "works_ad", "works_au")

_ADD_COLUMNS: Tuple[Tuple[str, str, str], ...] = (
    ("works", "author_text", "TEXT"),
    ("works", "year", "TEXT"),
    ("works", "publisher", "TEXT"),
    ("works", "journal", "TEXT"),
    ("works", "volume", "TEXT"),
    ("works", "issue", "TEXT"),
    ("works", "pages", "TEXT"),
    ("works", "isbn", "TEXT"),
    ("works", "doi", "TEXT"),
    ("works", "last_opened_at", "TIMESTAMP"),
    ("works", "updated_at", "TIMESTAMP"),
    ("persons", "image_url", "TEXT"),
    ("persons", "link_wikipedia", "TEXT"),
    ("persons", "link_stanford_encyclopedia", "TEXT"),
    ("persons", "link_iep", "TEXT"),
    ("persons", "links_other", "TEXT"),
    ("persons", "birth_date", "TEXT"),
    ("persons", "death_date", "TEXT"),
    ("works", "doc_type", "TEXT"),
    ("works", "private_notes", "TEXT"),
    ("works", "thumb_page", "INTEGER"),
    ("folders", "private_notes", "TEXT"),
    ("works", "source_kind", "TEXT"),
    ("works", "source_url", "TEXT"),
    ("works", "source_mime", "TEXT"),
    ("works", "thumb_url", "TEXT"),
    ("works", "provider", "TEXT"),
    ("works", "provider_id", "TEXT"),
    ("works", "urldate", "TEXT"),
    ("works", "edition", "TEXT"),
    ("works", "hide_pdf_link_annotations", "INTEGER DEFAULT 0"),
    ("works", "location", "TEXT"),
    ("folders", "parent_id", "TEXT"),
    ("playlists", "original_url", "TEXT"),
    ("roles", "credit_name", "TEXT"),
    ("processing_files", "last_error", "TEXT"),
    ("processing_files", "imported_work_id", "TEXT"),
    ("processing_files", "imported_at", "TIMESTAMP"),
    ("processing_files", "discovered_at", "TIMESTAMP"),
    ("processing_files", "updated_at", "TIMESTAMP"),
    ("processing_files", "title", "TEXT"),
    ("processing_files", "status_draft", "TEXT"),
    ("processing_files", "published_date", "TEXT"),
    ("processing_files", "abstract", "TEXT"),
    ("processing_files", "source_url", "TEXT"),
    ("processing_files", "author_text", "TEXT"),
    ("processing_files", "year", "TEXT"),
    ("processing_files", "publisher", "TEXT"),
    ("processing_files", "location", "TEXT"),
    ("processing_files", "edition", "TEXT"),
    ("processing_files", "journal", "TEXT"),
    ("processing_files", "volume", "TEXT"),
    ("processing_files", "issue", "TEXT"),
    ("processing_files", "pages", "TEXT"),
    ("processing_files", "isbn", "TEXT"),
    ("processing_files", "doi", "TEXT"),
    ("processing_files", "doc_type", "TEXT"),
    ("processing_files", "private_notes", "TEXT"),
    ("processing_files", "thumb_page", "INTEGER"),
    ("processing_files", "target_folder_id", "TEXT"),
    ("publishers", "created_at", "TIMESTAMP"),
)

_INDEX_SQL: Dict[str, str] = {
    "idx_playlist_items_work_unique": (
        "CREATE UNIQUE INDEX idx_playlist_items_work_unique ON playlist_items(work_id)"
    ),
    "idx_person_groups_name_nocase": (
        "CREATE UNIQUE INDEX idx_person_groups_name_nocase "
        "ON person_groups(name COLLATE NOCASE)"
    ),
    "idx_tags_name_nocase": (
        "CREATE UNIQUE INDEX idx_tags_name_nocase ON tags(name COLLATE NOCASE)"
    ),
    "idx_publishers_name_nocase": (
        "CREATE UNIQUE INDEX idx_publishers_name_nocase ON publishers(name COLLATE NOCASE)"
    ),
    "idx_publisher_aliases_alias_nocase": (
        "CREATE UNIQUE INDEX idx_publisher_aliases_alias_nocase "
        "ON publisher_aliases(alias COLLATE NOCASE)"
    ),
    "idx_roles_work_id": "CREATE INDEX idx_roles_work_id ON roles(work_id)",
    "idx_roles_person_id": "CREATE INDEX idx_roles_person_id ON roles(person_id)",
    "idx_annotations_work_id": (
        "CREATE INDEX idx_annotations_work_id ON annotations(work_id)"
    ),
    "idx_arguments_work_id": "CREATE INDEX idx_arguments_work_id ON arguments(work_id)",
    "idx_playlist_items_playlist_id": (
        "CREATE INDEX idx_playlist_items_playlist_id ON playlist_items(playlist_id)"
    ),
    "idx_works_last_opened_at": (
        "CREATE INDEX idx_works_last_opened_at ON works(last_opened_at)"
    ),
    "idx_folders_parent_id": "CREATE INDEX idx_folders_parent_id ON folders(parent_id)",
    "idx_folders_parent_title_nocase": (
        "CREATE UNIQUE INDEX idx_folders_parent_title_nocase "
        "ON folders(COALESCE(parent_id, ''), LOWER(TRIM(title)))"
    ),
    "idx_processing_files_status": (
        "CREATE INDEX idx_processing_files_status ON processing_files(status)"
    ),
    "idx_tag_aliases_alias_nocase": (
        "CREATE UNIQUE INDEX idx_tag_aliases_alias_nocase "
        "ON tag_aliases(alias COLLATE NOCASE)"
    ),
}

_UNIQUE_PREFLIGHT: Tuple[Tuple[str, str, str], ...] = (
    (
        "playlist_items",
        "idx_playlist_items_work_unique",
        "SELECT 1 FROM playlist_items GROUP BY work_id HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "person_groups",
        "idx_person_groups_name_nocase",
        "SELECT 1 FROM person_groups GROUP BY name COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "folders",
        "idx_folders_parent_title_nocase",
        "SELECT 1 FROM folders "
        "GROUP BY COALESCE(parent_id, ''), LOWER(TRIM(title)) HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "publisher_aliases",
        "idx_publisher_aliases_alias_nocase",
        "SELECT 1 FROM publisher_aliases GROUP BY alias COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "publishers",
        "idx_publishers_name_nocase",
        "SELECT 1 FROM publishers GROUP BY name COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "tags",
        "idx_tags_name_nocase",
        "SELECT 1 FROM tags GROUP BY name COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1",
    ),
    (
        "tag_aliases",
        "idx_tag_aliases_alias_nocase",
        "SELECT 1 FROM tag_aliases GROUP BY alias COLLATE NOCASE HAVING COUNT(*) > 1 LIMIT 1",
    ),
)

_TABLE_PKS: Dict[str, Tuple[str, ...]] = {
    "publishers": ("id",),
    "publisher_aliases": ("id",),
    "processing_file_tags": ("processing_file_id", "tag_id"),
}

# (from_column, parent_table, to_column, on_delete)
_TABLE_FKS: Dict[str, Tuple[Tuple[str, str, str, str], ...]] = {
    "publisher_aliases": (("publisher_id", "publishers", "id", "CASCADE"),),
    "processing_file_tags": (
        ("processing_file_id", "processing_files", "id", "CASCADE"),
        ("tag_id", "tags", "id", "CASCADE"),
    ),
}

_ACTIVE_SCHEMA_SQL: Optional[str] = None


class MigrationError(Exception):
    """Schema migration or validation failed. ``code`` is a safe machine reason."""

    def __init__(self, code: str, message: str, **details: str):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class Migration:
    target_version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def trigger_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for row in conn.execute(f"PRAGMA table_info({_ident(table)})"):
        if _row_field(row, 1, "name") == column:
            return True
    return False


def is_fresh_database(conn: sqlite3.Connection) -> bool:
    return not _user_table_names(conn)


def is_legacy_prks_database(conn: sqlite3.Connection) -> bool:
    """Unversioned file is PRKS only if it has works plus another established table."""
    names = set(_user_table_names(conn))
    if _LEGACY_MARKER_CORE not in names:
        return False
    return bool(names & _LEGACY_MARKER_COMPANIONS)


def read_schema_version(conn: sqlite3.Connection) -> int:
    """Strict schema_version reader. Duplicate identical integers are accepted."""
    if not table_exists(conn, "schema_version"):
        if _user_table_names(conn):
            return 0
        return 0
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    if not rows:
        return 0
    values: List[int] = []
    for row in rows:
        values.append(_parse_version_value(_row_field(row, 0, "version")))
    unique = set(values)
    if len(unique) > 1:
        raise MigrationError(
            "invalid_schema_version",
            "Database schema_version has conflicting values.",
        )
    version = values[0]
    if version > LATEST_SCHEMA_VERSION:
        raise MigrationError("newer_schema", NEWER_SCHEMA_MESSAGE)
    return version


def validate_migration_registry(
    migrations: Sequence[Migration] | None = None,
    *,
    latest: int | None = None,
    baseline: int = LEGACY_BASELINE_VERSION,
) -> None:
    items = tuple(MIGRATIONS if migrations is None else migrations)
    ceiling = LATEST_SCHEMA_VERSION if latest is None else latest
    if not items:
        if ceiling != baseline:
            raise MigrationError(
                "invalid_registry",
                "Migration registry is empty but latest schema version is not the baseline.",
            )
        return
    versions = [m.target_version for m in migrations_or_raise(items)]
    names = [m.name for m in items]
    if len(set(names)) != len(names):
        raise MigrationError("invalid_registry", "Migration names must be unique.")
    if versions[0] != baseline + 1:
        raise MigrationError(
            "invalid_registry",
            "Ordered migrations must start immediately after the legacy baseline.",
        )
    for previous, current in zip(versions, versions[1:]):
        if current != previous + 1:
            raise MigrationError(
                "invalid_registry",
                "Ordered migrations must be contiguous and increasing.",
            )
    if versions[-1] != ceiling:
        raise MigrationError(
            "invalid_registry",
            "Last migration target must equal the latest schema version.",
        )


def migrations_or_raise(items: Sequence[Migration]) -> Sequence[Migration]:
    seen_targets = set()
    previous = None
    for item in items:
        if not isinstance(item.target_version, int) or item.target_version <= 0:
            raise MigrationError("invalid_registry", "Migration target versions must be positive integers.")
        if item.target_version in seen_targets:
            raise MigrationError("invalid_registry", "Duplicate migration target version.")
        seen_targets.add(item.target_version)
        if previous is not None and item.target_version <= previous:
            raise MigrationError("invalid_registry", "Migration target versions must strictly increase.")
        if not _MIGRATION_NAME_RE.fullmatch(item.name):
            raise MigrationError("invalid_registry", "Migration name is not a safe identifier.")
        previous = item.target_version
    return items


def ensure_database_schema(conn: sqlite3.Connection, schema_path: str) -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    previous_isolation = conn.isolation_level
    previous_factory = conn.row_factory
    global _ACTIVE_SCHEMA_SQL
    previous_schema = _ACTIVE_SCHEMA_SQL
    try:
        conn.isolation_level = None
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _ACTIVE_SCHEMA_SQL = schema_sql
        if is_fresh_database(conn):
            _bootstrap_fresh(conn, schema_sql)
            return
        if table_exists(conn, "schema_version"):
            version = read_schema_version(conn)
            _normalize_identical_version_rows(conn, version)
        elif is_legacy_prks_database(conn):
            version = 0
        else:
            raise MigrationError(
                "not_prks_database",
                "This file is not a PRKS database.",
            )
        if version < LEGACY_BASELINE_VERSION:
            _run_legacy_bridge(conn, schema_sql)
            version = LEGACY_BASELINE_VERSION
        if version == LATEST_SCHEMA_VERSION:
            validate_current_schema(conn)
            return
        apply_ordered_migrations(conn, version)
        validate_current_schema(conn)
    finally:
        _ACTIVE_SCHEMA_SQL = previous_schema
        conn.row_factory = previous_factory
        conn.isolation_level = previous_isolation


def apply_ordered_migrations(
    conn: sqlite3.Connection,
    current_version: int,
    migrations: Sequence[Migration] | None = None,
) -> int:
    """Apply registry entries after ``current_version``. Each entry is one transaction."""
    items = tuple(MIGRATIONS if migrations is None else migrations)
    previous_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        version = current_version
        for migration in items:
            if migration.target_version <= version:
                continue
            if migration.target_version != version + 1:
                raise MigrationError(
                    "migration_gap",
                    "Ordered migrations must be applied without gaps.",
                )
            _run_one_migration(conn, migration, from_version=version)
            version = migration.target_version
        return version
    finally:
        conn.isolation_level = previous_isolation


def validate_current_schema(conn: sqlite3.Connection) -> None:
    version = read_schema_version(conn)
    if version != LATEST_SCHEMA_VERSION:
        raise MigrationError(
            "schema_drift",
            "Database schema_version does not match the current PRKS schema.",
        )
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    if len(rows) != 1:
        raise MigrationError(
            "schema_drift",
            "Database schema_version is not a single row.",
        )
    for table in REQUIRED_TABLES:
        if not table_exists(conn, table):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=table)
    for table, columns in REQUIRED_COLUMNS.items():
        if not table_exists(conn, table):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=table)
        present = set(_table_column_names(conn, table))
        for column in columns:
            if column not in present:
                raise MigrationError(
                    "schema_drift",
                    "Required schema object is missing.",
                    object=f"{table}.{column}",
                )
    for spec in INDEX_SPECS:
        if not _index_matches_spec(conn, spec):
            raise MigrationError(
                "schema_drift",
                "Required schema object is missing or has the wrong definition.",
                object=spec.name,
            )
    for table, required_fks in _TABLE_FKS.items():
        if not table_exists(conn, table):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=table)
        present = _foreign_key_tuples(conn, table)
        for expected in required_fks:
            if expected not in present:
                raise MigrationError(
                    "schema_drift",
                    "Required schema object is missing or has the wrong definition.",
                    object=table,
                )
    fts_cols = set(_fts_column_names(conn))
    for column in REQUIRED_FTS_COLUMNS:
        if column not in fts_cols:
            raise MigrationError(
                "schema_drift",
                "Required schema object is missing.",
                object=f"works_fts.{column}",
            )
    for trigger_name in REQUIRED_FTS_TRIGGERS:
        if not trigger_exists(conn, trigger_name):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=trigger_name)
        sql = _master_sql(conn, "trigger", trigger_name) or ""
        if "author_text" not in sql:
            raise MigrationError("schema_drift", "Required schema object is missing.", object=trigger_name)
    if index_exists(conn, "idx_folders_title_nocase"):
        raise MigrationError(
            "schema_drift",
            "Obsolete schema object is still present.",
            object="idx_folders_title_nocase",
        )


def application_schema_signature(conn: sqlite3.Connection) -> dict:
    tables = []
    columns: Dict[str, List[str]] = {}
    for name in sorted(_user_table_names(conn)):
        if _is_fts_shadow(name):
            continue
        tables.append(name)
        columns[name] = sorted(_table_column_names(conn, name))
    indexes = sorted(
        _row_field(row, 0, "name")
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name NOT LIKE 'sqlite_%' AND name IS NOT NULL"
        )
    )
    triggers = sorted(
        _row_field(row, 0, "name")
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IS NOT NULL"
        )
    )
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "triggers": triggers,
        "fts_columns": sorted(_fts_column_names(conn)) if table_exists(conn, "works_fts") else [],
    }


def migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    reconcile_pre_v10_schema(conn, _require_schema_sql())


def normalize_legacy_database_to_v9(conn: sqlite3.Connection) -> None:
    reconcile_pre_v10_schema(conn, _require_schema_sql())
    _set_schema_version(conn, LEGACY_BASELINE_VERSION)


def reconcile_pre_v10_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    _ensure_missing_tables(conn, schema_sql)
    _ensure_historical_columns(conn)
    _normalize_doc_type(conn)
    _migrate_tags_case_dedupe(conn)
    conn.execute("DROP INDEX IF EXISTS idx_folders_title_nocase")
    _preflight_unique_indexes(conn)
    _ensure_required_indexes(conn)
    _ensure_works_fts(conn)
    _assert_known_table_shapes(conn)


def _require_schema_sql() -> str:
    if _ACTIVE_SCHEMA_SQL is not None:
        return _ACTIVE_SCHEMA_SQL
    return Path(__file__).with_name("db_schema.sql").read_text(encoding="utf-8")


def _bootstrap_fresh(conn: sqlite3.Connection, schema_sql: str) -> None:
    def _apply() -> None:
        for stmt in iter_sql_statements(schema_sql):
            kind = _statement_kind(stmt)
            if kind == "pragma":
                continue
            conn.execute(stmt)
        _set_schema_version(conn, LATEST_SCHEMA_VERSION)
        validate_current_schema(conn)

    _run_in_transaction(conn, _apply)


def _run_legacy_bridge(conn: sqlite3.Connection, schema_sql: str) -> None:
    from_version = read_schema_version(conn)
    LOGGER.info(
        "db_legacy_normalization_started from_version=%s to_version=%s",
        from_version,
        LEGACY_BASELINE_VERSION,
    )

    def _apply() -> None:
        reconcile_pre_v10_schema(conn, schema_sql)
        _set_schema_version(conn, LEGACY_BASELINE_VERSION)

    try:
        _run_in_transaction(conn, _apply)
    except Exception as exc:
        LOGGER.error(
            "db_legacy_normalization_failed from_version=%s to_version=%s error_type=%s",
            from_version,
            LEGACY_BASELINE_VERSION,
            safe_error_type(exc),
        )
        raise
    LOGGER.info(
        "db_legacy_normalization_completed from_version=%s to_version=%s",
        from_version,
        LEGACY_BASELINE_VERSION,
    )


def _run_one_migration(conn: sqlite3.Connection, migration: Migration, *, from_version: int) -> None:
    name = safe_log_label(migration.name)
    LOGGER.info(
        "db_migration_started from_version=%s to_version=%s migration=%s",
        from_version,
        migration.target_version,
        name,
    )

    def _apply() -> None:
        migration.apply(conn)
        if migration.target_version == LATEST_SCHEMA_VERSION:
            _set_schema_version(conn, migration.target_version)
            validate_current_schema(conn)
        else:
            _set_schema_version(conn, migration.target_version)

    try:
        _run_in_transaction(conn, _apply)
    except Exception as exc:
        extra = ""
        if isinstance(exc, MigrationError) and exc.code:
            extra = " reason=%s" % safe_log_label(exc.code)
            constraint = exc.details.get("constraint")
            if constraint:
                extra += " constraint=%s" % safe_log_label(constraint)
        LOGGER.error(
            "db_migration_failed from_version=%s to_version=%s migration=%s error_type=%s%s",
            from_version,
            migration.target_version,
            name,
            safe_error_type(exc),
            extra,
        )
        raise
    LOGGER.info(
        "db_migration_completed from_version=%s to_version=%s migration=%s",
        from_version,
        migration.target_version,
        name,
    )


def _run_in_transaction(conn: sqlite3.Connection, fn: Callable[[], None]) -> None:
    if conn.in_transaction:
        raise MigrationError("internal", "Migration runner expected no open transaction.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        fn()
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _normalize_identical_version_rows(conn: sqlite3.Connection, version: int) -> None:
    if not table_exists(conn, "schema_version"):
        return
    count_row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    count = int(_row_field(count_row, 0, "COUNT(*)") or 0)
    if count <= 1:
        return

    def _apply() -> None:
        _set_schema_version(conn, version)

    _run_in_transaction(conn, _apply)


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    if not table_exists(conn, "schema_version"):
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _ensure_missing_tables(conn: sqlite3.Connection, schema_sql: str) -> None:
    for stmt in iter_sql_statements(schema_sql):
        kind, name = _classify_ddl(stmt)
        if kind != "table" or name is None:
            continue
        if name == "works_fts":
            continue
        if table_exists(conn, name):
            continue
        conn.execute(stmt)


def _ensure_historical_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADD_COLUMNS:
        if not table_exists(conn, table):
            continue
        if column_exists(conn, table, column):
            continue
        conn.execute(
            f"ALTER TABLE {_ident(table)} ADD COLUMN {_ident(column)} {decl}"
        )


def _normalize_doc_type(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "works") or not column_exists(conn, "works", "doc_type"):
        return
    conn.execute(
        "UPDATE works SET doc_type = 'article' WHERE doc_type IS NULL OR TRIM(doc_type) = ''"
    )


def _migrate_tags_case_dedupe(conn: sqlite3.Connection) -> None:
    """Merge tags that differ only by letter case; keep earliest created_at then smallest id."""
    if not table_exists(conn, "tags"):
        return
    rows = conn.execute("SELECT id, name, created_at FROM tags").fetchall()
    groups: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        rid = str(_row_field(row, 0, "id") or "")
        name = str(_row_field(row, 1, "name") or "")
        created = str(_row_field(row, 2, "created_at") or "")
        key = name.strip().lower()
        if not key:
            continue
        groups[key].append((rid, name, created))
    has_work_tags = table_exists(conn, "work_tags")
    has_folder_tags = table_exists(conn, "folder_tags")
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: (item[2], item[0]))
        keeper = members[0][0]
        for loser_id, _name, _created in members[1:]:
            if has_work_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO work_tags (work_id, tag_id) "
                    "SELECT work_id, ? FROM work_tags WHERE tag_id = ?",
                    (keeper, loser_id),
                )
                conn.execute("DELETE FROM work_tags WHERE tag_id = ?", (loser_id,))
            if has_folder_tags:
                conn.execute(
                    "INSERT OR IGNORE INTO folder_tags (folder_id, tag_id) "
                    "SELECT folder_id, ? FROM folder_tags WHERE tag_id = ?",
                    (keeper, loser_id),
                )
                conn.execute("DELETE FROM folder_tags WHERE tag_id = ?", (loser_id,))
            conn.execute("DELETE FROM tags WHERE id = ?", (loser_id,))


def _preflight_unique_indexes(conn: sqlite3.Connection) -> None:
    for table, index_name, sql in _UNIQUE_PREFLIGHT:
        if not table_exists(conn, table):
            continue
        spec = _index_spec(index_name)
        if spec is not None and _index_matches_spec(conn, spec):
            continue
        conflict = conn.execute(sql).fetchone()
        if conflict is not None:
            constraint = index_name.removeprefix("idx_")
            raise MigrationError(
                "legacy_constraint_conflict",
                "Legacy database has conflicting rows for a uniqueness constraint.",
                constraint=constraint,
            )


def _ensure_required_indexes(conn: sqlite3.Connection) -> None:
    for spec in INDEX_SPECS:
        if _index_matches_spec(conn, spec):
            continue
        if not table_exists(conn, spec.table):
            continue
        if index_exists(conn, spec.name):
            conn.execute(f"DROP INDEX {_ident(spec.name)}")
        conn.execute(_INDEX_SQL[spec.name])


def _ensure_works_fts(conn: sqlite3.Connection) -> None:
    needs_rebuild = False
    if not table_exists(conn, "works_fts"):
        needs_rebuild = True
    else:
        cols = _fts_column_names(conn)
        if any(column not in cols for column in REQUIRED_FTS_COLUMNS):
            needs_rebuild = True
        for trigger_name in REQUIRED_FTS_TRIGGERS:
            if not trigger_exists(conn, trigger_name):
                needs_rebuild = True
                continue
            sql = _master_sql(conn, "trigger", trigger_name) or ""
            if "author_text" not in sql:
                needs_rebuild = True
    if not needs_rebuild:
        return
    _rebuild_works_fts(conn)


def _rebuild_works_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS works_ai")
    conn.execute("DROP TRIGGER IF EXISTS works_ad")
    conn.execute("DROP TRIGGER IF EXISTS works_au")
    conn.execute("DROP TABLE IF EXISTS works_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE works_fts USING fts5(
            title,
            abstract,
            text_content,
            author_text,
            content='works',
            content_rowid='rowid'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER works_ai AFTER INSERT ON works BEGIN
          INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
          VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER works_ad AFTER DELETE ON works BEGIN
          INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
          VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER works_au AFTER UPDATE ON works BEGIN
          INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
          VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
          INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
          VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
        END
        """
    )
    if table_exists(conn, "works") and column_exists(conn, "works", "author_text"):
        conn.execute(
            """
            INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
            SELECT rowid, title, abstract, text_content, COALESCE(author_text, '') FROM works
            """
        )


def _assert_known_table_shapes(conn: sqlite3.Connection) -> None:
    for table, pk_cols in _TABLE_PKS.items():
        if not table_exists(conn, table):
            raise MigrationError("incompatible_table", "Required schema object is missing.", object=table)
        required = REQUIRED_COLUMNS.get(table, ())
        present = set(_table_column_names(conn, table))
        for column in required:
            if column not in present:
                raise MigrationError(
                    "incompatible_table",
                    "Existing table is missing a required column.",
                    object=f"{table}.{column}",
                )
        actual_pk = _table_pk_columns(conn, table)
        if actual_pk != pk_cols:
            raise MigrationError(
                "incompatible_table",
                "Existing table has an incompatible primary key.",
                object=table,
            )
    for table, required_fks in _TABLE_FKS.items():
        if not table_exists(conn, table):
            raise MigrationError("incompatible_table", "Required schema object is missing.", object=table)
        present = _foreign_key_tuples(conn, table)
        for expected in required_fks:
            if expected not in present:
                raise MigrationError(
                    "incompatible_table",
                    "Existing table has an incompatible foreign key.",
                    object=table,
                )


def _fts_column_names(conn: sqlite3.Connection) -> List[str]:
    if not table_exists(conn, "works_fts"):
        return []
    names = _table_column_names(conn, "works_fts")
    if names:
        return names
    sql = _master_sql(conn, "table", "works_fts") or ""
    match = re.search(r"fts5\s*\((.*)\)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    cols: List[str] = []
    for part in match.group(1).split(","):
        token = part.strip().split("=")[0].strip().strip('"')
        if token and _IDENT_RE.fullmatch(token):
            cols.append(token)
    return cols


def _table_column_names(conn: sqlite3.Connection, table: str) -> List[str]:
    return [
        str(_row_field(row, 1, "name"))
        for row in conn.execute(f"PRAGMA table_info({_ident(table)})")
    ]


def _table_pk_columns(conn: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    ranked: List[Tuple[int, str]] = []
    for row in conn.execute(f"PRAGMA table_info({_ident(table)})"):
        pk = int(_row_field(row, 5, "pk") or 0)
        if pk > 0:
            ranked.append((pk, str(_row_field(row, 1, "name"))))
    ranked.sort()
    return tuple(name for _order, name in ranked)


def _foreign_key_tuples(conn: sqlite3.Connection, table: str) -> set[Tuple[str, str, str, str]]:
    found: set[Tuple[str, str, str, str]] = set()
    for row in conn.execute(f"PRAGMA foreign_key_list({_ident(table)})"):
        parent = str(_row_field(row, 2, "table") or "")
        src = str(_row_field(row, 3, "from") or "")
        dest = str(_row_field(row, 4, "to") or "")
        on_delete = str(_row_field(row, 6, "on_delete") or "NO ACTION").upper()
        found.add((src, parent, dest, on_delete))
    return found


def _user_table_names(conn: sqlite3.Connection) -> List[str]:
    names = []
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        name = str(_row_field(row, 0, "name") or "")
        if not name or name.startswith("sqlite_"):
            continue
        names.append(name)
    return names


def _is_fts_shadow(name: str) -> bool:
    return name.startswith("works_fts_")


def _master_sql(conn: sqlite3.Connection, kind: str, name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (kind, name),
    ).fetchone()
    if row is None:
        return None
    sql = _row_field(row, 0, "sql")
    return str(sql) if sql is not None else None


def _parse_version_value(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationError(
            "invalid_schema_version",
            "Database schema_version is not a valid integer.",
        )
    if value < 0:
        raise MigrationError(
            "invalid_schema_version",
            "Database schema_version is not a valid integer.",
        )
    return int(value)


def _ident(name: str) -> str:
    if not _IDENT_RE.fullmatch(name):
        raise MigrationError("invalid_schema_object", "Invalid schema object name.")
    return '"' + name + '"'


def _row_field(row, index: int, key: str):
    if row is None:
        return None
    try:
        return row[key]
    except (TypeError, IndexError, KeyError):
        return row[index]


def _index_spec(name: str) -> Optional[IndexSpec]:
    for spec in INDEX_SPECS:
        if spec.name == name:
            return spec
    return None


def _index_owner_table(conn: sqlite3.Connection, name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return str(_row_field(row, 0, "tbl_name") or "") or None


def _index_is_unique(conn: sqlite3.Connection, name: str, table: str) -> bool:
    for row in conn.execute(f"PRAGMA index_list({_ident(table)})"):
        if _row_field(row, 1, "name") == name:
            return bool(int(_row_field(row, 2, "unique") or 0))
    return False


def _index_key_columns(conn: sqlite3.Connection, name: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    columns: List[str] = []
    collations: List[str] = []
    for row in conn.execute(f"PRAGMA index_xinfo({_ident(name)})"):
        raw_key = _row_field(row, 5, "key")
        try:
            key = 1 if raw_key is None else int(raw_key)
        except (TypeError, ValueError):
            key = 1
        if key == 0:
            continue
        cid = int(_row_field(row, 1, "cid") or 0)
        col_name = _row_field(row, 2, "name")
        coll = str(_row_field(row, 4, "coll") or "BINARY")
        if cid < 0 or col_name is None:
            continue
        columns.append(str(col_name))
        collations.append(coll.upper())
    return tuple(columns), tuple(collations)


def _normalize_index_sql(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    text = text.replace('"', "")
    return re.sub(r"\s+", "", text).upper()


def _index_matches_spec(conn: sqlite3.Connection, spec: IndexSpec) -> bool:
    if not index_exists(conn, spec.name):
        return False
    owner = _index_owner_table(conn, spec.name)
    if owner != spec.table:
        return False
    if _index_is_unique(conn, spec.name, spec.table) != spec.unique:
        return False
    if spec.expression_tokens:
        sql = _master_sql(conn, "index", spec.name) or ""
        normalized = _normalize_index_sql(sql)
        return all(_normalize_index_sql(token) in normalized for token in spec.expression_tokens)
    columns, collations = _index_key_columns(conn, spec.name)
    if columns != spec.columns:
        return False
    expected = tuple((coll or "BINARY").upper() for coll in spec.collations)
    if not expected:
        expected = tuple("BINARY" for _ in spec.columns)
    return collations == expected


def _index_table_name(index_name: str) -> Optional[str]:
    spec = _index_spec(index_name)
    return spec.table if spec is not None else None


def _statement_kind(stmt: str) -> str:
    kind, _name = _classify_ddl(stmt)
    return kind


def _classify_ddl(stmt: str) -> Tuple[str, Optional[str]]:
    compact = _leading_sql(stmt)
    if compact.upper().startswith("PRAGMA"):
        return "pragma", None
    match = _CREATE_VTABLE_RE.match(compact)
    if match:
        return "virtual_table", match.group(1)
    match = _CREATE_TABLE_RE.match(compact)
    if match:
        return "table", match.group(1)
    match = _CREATE_INDEX_RE.match(compact)
    if match:
        return "index", match.group(1)
    match = _CREATE_TRIGGER_RE.match(compact)
    if match:
        return "trigger", match.group(1)
    return "other", None


def _leading_sql(stmt: str) -> str:
    text = stmt.strip()
    while True:
        if text.startswith("--"):
            nl = text.find("\n")
            text = text[nl + 1 :].lstrip() if nl >= 0 else ""
            continue
        if text.startswith("/*"):
            end = text.find("*/")
            text = text[end + 2 :].lstrip() if end >= 0 else ""
            continue
        break
    return text


def _keyword_at(text: str, index: int, word: str) -> bool:
    size = len(word)
    if text[index:index + size].upper() != word.upper():
        return False
    if index > 0 and _is_ident_char(text[index - 1]):
        return False
    end = index + size
    if end < len(text) and _is_ident_char(text[end]):
        return False
    return True


def _is_ident_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def iter_sql_statements(script: str) -> List[str]:
    """Split schema SQL into statements, keeping CREATE TRIGGER bodies intact."""
    statements: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(script)
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    in_trigger = False
    trigger_begin_depth = 0
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"' and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "-" and nxt == "-":
            buf.extend(("-", "-"))
            i += 2
            in_line_comment = True
            continue
        if ch == "/" and nxt == "*":
            buf.extend(("/", "*"))
            i += 2
            in_block_comment = True
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if not in_trigger and _keyword_at(script, i, "CREATE"):
            j = i + 6
            while j < n and script[j].isspace():
                j += 1
            if _keyword_at(script, j, "TEMP") or _keyword_at(script, j, "TEMPORARY"):
                j += 4 if _keyword_at(script, j, "TEMP") and not _keyword_at(script, j, "TEMPORARY") else 9
                while j < n and script[j].isspace():
                    j += 1
            if _keyword_at(script, j, "TRIGGER"):
                in_trigger = True
        if in_trigger and _keyword_at(script, i, "BEGIN"):
            trigger_begin_depth += 1
        elif in_trigger and trigger_begin_depth and _keyword_at(script, i, "END"):
            trigger_begin_depth -= 1
        if ch == ";" and trigger_begin_depth == 0:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            in_trigger = False
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


MIGRATIONS: Tuple[Migration, ...] = (
    Migration(
        target_version=10,
        name="ordered_migration_baseline",
        apply=migrate_v9_to_v10,
    ),
)

validate_migration_registry()
