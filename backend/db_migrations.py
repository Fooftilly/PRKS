"""Ordered SQLite schema migrations for PRKS.

Fresh databases are created from db_schema.sql at LATEST_SCHEMA_VERSION.
Existing databases are inspected, then upgraded through an explicit registry.

Versions below LEGACY_BASELINE_VERSION have no reconstructable step history.
They are normalized to the v9 compatibility baseline once, then the ordered
9→10 migration enforces the current schema contract.

Migrations may modify SQLite state only — never managed filesystem data.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backend.log_safety import safe_error_type, safe_log_label

LOGGER = logging.getLogger("prks.db")

LATEST_SCHEMA_VERSION = 17
LEGACY_BASELINE_VERSION = 9

# Unversioned files count as PRKS only with works plus another established table.
# Do not treat an arbitrary SQLite DB as a legacy library.
#
# Current schema does not require work_annotations (removed in v13).
# Legacy recognition MAY still use that historical table name.
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
    "concept_aliases",
    "concept_parents",
    "positions",
    "arguments",
    "argument_verdicts",
    "argument_sources",
    "argument_target_positions",
    "argument_target_arguments",
    "roles",
    "annotations",
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
    "saved_views",
    "sync_operations",
    "sync_entity_revisions",
    "sync_tag_lifecycle",
    "pending_pdf_cleanup",
    # #60 Slice A (schema 17): Work -> Manifestation -> Asset identity layer.
    "manifestations",
    "assets",
    "manifestation_relations",
    "manifestation_identifiers",
    "sync_work_lifecycle",
    "work_retirement_guard",
    "work_retirement",
    "migration_quarantine",
    "legacy_inferred_video_urls",
)

REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    # Work deletion writes its post-delete PDF claim inside the same
    # transaction as the row delete, so a missing table must be loud schema
    # drift at startup rather than a surprise at the first deletion.
    "pending_pdf_cleanup": ("filename", "recorded_at", "last_attempt_at"),
    "sync_operations": ("op_id", "device_id", "operation_type", "entity_type", "entity_id", "request_hash", "status", "http_status", "result_json", "applied_at"),
    "sync_entity_revisions": ("scope_type", "scope_id", "revision", "updated_at"),
    "sync_tag_lifecycle": ("tag_id", "state", "target_tag_id", "changed_at"),
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
        "canonical_annotation_set_revision",
        "materialized_pdf_annotation_revision",
        "primary_manifestation_id",
        "citation_manifestation_id",
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
    "roles": ("person_id", "work_id", "role_type", "order_index", "credit_name", "manifestation_id"),
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
    "saved_views": (
        "id",
        "name",
        "mode",
        "search_q",
        "search_tag",
        "search_author",
        "search_publisher",
        "created_at",
        "updated_at",
    ),
    "concepts": ("id", "name", "description", "created_at", "updated_at"),
    "concept_aliases": ("concept_id", "alias", "normalized_alias", "created_at"),
    "concept_parents": ("child_concept_id", "parent_concept_id", "created_at"),
    "positions": ("id", "name", "description", "created_at", "updated_at"),
    "arguments": ("id", "name", "kind", "main_text", "created_at", "updated_at"),
    "argument_verdicts": ("id", "label", "sort_order", "enabled"),
    "argument_sources": (
        "argument_id", "order_index", "work_id", "manifestation_id", "pages", "created_at",
    ),
    "argument_target_positions": (
        "argument_id",
        "position_id",
        "verdict_id",
        "order_index",
        "created_at",
    ),
    "argument_target_arguments": (
        "argument_id",
        "target_argument_id",
        "verdict_id",
        "order_index",
        "created_at",
    ),
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
    IndexSpec(
        "idx_roles_person_work_role_unique",
        "roles",
        True,
        columns=("person_id", "work_id", "role_type"),
    ),
    IndexSpec(
        "idx_saved_views_name_nocase",
        "saved_views",
        True,
        columns=("name",),
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
        "idx_concept_aliases_normalized",
        "concept_aliases",
        True,
        columns=("normalized_alias",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_concept_parents_parent",
        "concept_parents",
        False,
        columns=("parent_concept_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_argument_sources_work_id",
        "argument_sources",
        False,
        columns=("work_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_argument_target_arguments_target",
        "argument_target_arguments",
        False,
        columns=("target_argument_id",),
        collations=("BINARY",),
    ),
    IndexSpec(
        "idx_argument_target_positions_position",
        "argument_target_positions",
        False,
        columns=("position_id",),
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
    # The relationship identity the whole product uses. The table's primary key
    # includes `order_index`, so SQLite would happily hold two rows for one
    # (person, work, role) -- one revision scope describing two rows. Only
    # application code prevented it, which a direct or legacy write bypasses.
    "idx_roles_person_work_role_unique": (
        "CREATE UNIQUE INDEX idx_roles_person_work_role_unique "
        "ON roles(person_id, work_id, role_type)"
    ),
    "idx_roles_person_id": "CREATE INDEX idx_roles_person_id ON roles(person_id)",
    "idx_annotations_work_id": (
        "CREATE INDEX idx_annotations_work_id ON annotations(work_id)"
    ),
    "idx_concept_aliases_normalized": (
        "CREATE UNIQUE INDEX idx_concept_aliases_normalized "
        "ON concept_aliases(normalized_alias)"
    ),
    "idx_concept_parents_parent": (
        "CREATE INDEX idx_concept_parents_parent ON concept_parents(parent_concept_id)"
    ),
    "idx_argument_sources_work_id": (
        "CREATE INDEX idx_argument_sources_work_id ON argument_sources(work_id)"
    ),
    "idx_argument_target_arguments_target": (
        "CREATE INDEX idx_argument_target_arguments_target "
        "ON argument_target_arguments(target_argument_id)"
    ),
    "idx_argument_target_positions_position": (
        "CREATE INDEX idx_argument_target_positions_position "
        "ON argument_target_positions(position_id)"
    ),
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
    "idx_saved_views_name_nocase": (
        "CREATE UNIQUE INDEX idx_saved_views_name_nocase "
        "ON saved_views(name COLLATE NOCASE)"
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
    (
        "roles",
        "idx_roles_person_work_role_unique",
        "SELECT 1 FROM roles GROUP BY person_id, work_id, role_type "
        "HAVING COUNT(*) > 1 LIMIT 1",
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

_POST_V13_TABLES = frozenset({"sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"})
# Created only by the v17 migration; the pre-v10 bridge must not create them early.
_POST_V16_TABLES = frozenset(
    {
        "manifestations",
        "assets",
        "manifestation_relations",
        "manifestation_identifiers",
        "sync_work_lifecycle",
        "work_retirement_guard",
        "work_retirement",
        "migration_quarantine",
        "legacy_inferred_video_urls",
    }
)
# The pre-v17 shape of the leaf tables v17 rebuilds. The pre-v10 bridge creates
# a missing one in this shape (the current db_schema.sql shape references
# tables that do not exist yet), and v17 then rebuilds it like any other.
_LEGACY_LEAF_TABLE_SQL = {
    "roles": """
CREATE TABLE roles (
    person_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    credit_name TEXT,
    PRIMARY KEY (person_id, work_id, role_type, order_index),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
)
""",
    "annotations": """
CREATE TABLE annotations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    type TEXT,
    content TEXT,
    page_index INTEGER,
    color TEXT,
    geometry_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
)
""",
}
_POST_V10_TABLES = frozenset({"saved_views"})
_POST_V11_TABLES = frozenset(
    {
        "concept_aliases",
        "concept_parents",
        "positions",
        "argument_verdicts",
        "argument_sources",
        "argument_target_positions",
        "argument_target_arguments",
    }
)
_LEGACY_ARGUMENTS_SQL = """
CREATE TABLE arguments (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    premise TEXT,
    conclusion TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
)
"""

# PKs/FKs enforced on the current (v12) schema. Pre-v10 reconcile uses _TABLE_PKS/_TABLE_FKS only.
_CURRENT_TABLE_PKS: Dict[str, Tuple[str, ...]] = {
    # The basename primary key is load-bearing, not decoration: the Work-delete
    # transaction records its claim with ON CONFLICT(filename) DO NOTHING, and
    # "one row per managed PDF" is what keeps repeated deletions and replays
    # from accumulating duplicate permanent rows. Validating the columns alone
    # would let a table that lost that uniqueness pass startup and then break
    # the canonical deletion.
    "pending_pdf_cleanup": ("filename",),
    "sync_operations": ("op_id",),
    "sync_entity_revisions": ("scope_type", "scope_id"),
    "sync_tag_lifecycle": ("tag_id",),
    "concepts": ("id",),
    "concept_aliases": ("concept_id", "normalized_alias"),
    "concept_parents": ("child_concept_id", "parent_concept_id"),
    "positions": ("id",),
    "arguments": ("id",),
    "argument_verdicts": ("id",),
    "argument_sources": ("argument_id", "order_index"),
    "argument_target_positions": ("argument_id", "position_id"),
    "argument_target_arguments": ("argument_id", "target_argument_id"),
}

_CURRENT_TABLE_FKS: Dict[str, Tuple[Tuple[str, str, str, str], ...]] = {
    # A cleanup claim must outlive the Work row that created it -- that is the
    # entire point -- so it deliberately carries no foreign key, and this
    # pins that rather than leaving it to be "helpfully" added later.
    "pending_pdf_cleanup": (),
    "sync_operations": (), "sync_entity_revisions": (), "sync_tag_lifecycle": (),
    "concept_aliases": (("concept_id", "concepts", "id", "CASCADE"),),
    "concept_parents": (
        ("child_concept_id", "concepts", "id", "CASCADE"),
        ("parent_concept_id", "concepts", "id", "CASCADE"),
    ),
    "argument_target_positions": (
        ("argument_id", "arguments", "id", "CASCADE"),
        ("position_id", "positions", "id", "RESTRICT"),
        ("verdict_id", "argument_verdicts", "id", "NO ACTION"),
    ),
    "argument_target_arguments": (
        ("argument_id", "arguments", "id", "CASCADE"),
        ("target_argument_id", "arguments", "id", "RESTRICT"),
        ("verdict_id", "argument_verdicts", "id", "NO ACTION"),
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
        if present != set(required_fks):
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
    if index_exists(conn, "idx_arguments_work_id"):
        raise MigrationError(
            "schema_drift",
            "Obsolete schema object is still present.",
            object="idx_arguments_work_id",
        )
    for table, pk_cols in _CURRENT_TABLE_PKS.items():
        if not table_exists(conn, table):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=table)
        actual_pk = _table_pk_columns(conn, table)
        if actual_pk != pk_cols:
            raise MigrationError(
                "schema_drift",
                "Required schema object is missing or has the wrong definition.",
                object=table,
            )
    for table, required_fks in _CURRENT_TABLE_FKS.items():
        if not table_exists(conn, table):
            raise MigrationError("schema_drift", "Required schema object is missing.", object=table)
        present = _foreign_key_tuples(conn, table)
        if present != set(required_fks):
            raise MigrationError(
                "schema_drift",
                "Required schema object is missing or has the wrong definition.",
                object=table,
            )
    _validate_work_identity_objects(conn)


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
    views = sorted(
        _row_field(row, 0, "name")
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view' AND name IS NOT NULL"
        )
    )
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "triggers": triggers,
        "views": views,
        "fts_columns": sorted(_fts_column_names(conn)) if table_exists(conn, "works_fts") else [],
    }


def migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    reconcile_pre_v10_schema(conn, _require_schema_sql())


def migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Retire the unused work_annotations snapshot. Do not import its JSON."""
    conn.execute("DROP TABLE IF EXISTS work_annotations")


def migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE concept_aliases (
            concept_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (concept_id, normalized_alias),
            FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_concept_aliases_normalized "
        "ON concept_aliases(normalized_alias)"
    )
    conn.execute(
        """
        CREATE TABLE concept_parents (
            child_concept_id TEXT NOT NULL,
            parent_concept_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (child_concept_id, parent_concept_id),
            CHECK (child_concept_id <> parent_concept_id),
            FOREIGN KEY (child_concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_concept_id) REFERENCES concepts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_concept_parents_parent ON concept_parents(parent_concept_id)"
    )
    conn.execute(
        """
        CREATE TABLE positions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE argument_verdicts (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        INSERT INTO argument_verdicts (id, label, sort_order, enabled) VALUES
            ('supports', 'Supports', 1, 1),
            ('opposes', 'Opposes', 2, 1),
            ('qualifies', 'Qualifies', 3, 1),
            ('holds', 'Holds', 4, 1)
        """
    )

    conn.execute("DROP INDEX IF EXISTS idx_arguments_work_id")
    has_legacy = table_exists(conn, "arguments") and column_exists(conn, "arguments", "work_id")
    if has_legacy:
        conn.execute("ALTER TABLE arguments RENAME TO arguments_legacy_v11")
    elif table_exists(conn, "arguments"):
        conn.execute("DROP TABLE arguments")

    conn.execute(
        """
        CREATE TABLE arguments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('argument', 'stance')),
            main_text TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE argument_sources (
            argument_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            pages TEXT NOT NULL DEFAULT '',
            order_index INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (argument_id, work_id),
            FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
            FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_argument_sources_work_id ON argument_sources(work_id)"
    )
    conn.execute(
        """
        CREATE TABLE argument_target_positions (
            argument_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            verdict_id TEXT NOT NULL,
            order_index INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (argument_id, position_id),
            FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
            FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE RESTRICT,
            FOREIGN KEY (verdict_id) REFERENCES argument_verdicts(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE argument_target_arguments (
            argument_id TEXT NOT NULL,
            target_argument_id TEXT NOT NULL,
            verdict_id TEXT NOT NULL,
            order_index INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (argument_id, target_argument_id),
            CHECK (argument_id <> target_argument_id),
            FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
            FOREIGN KEY (target_argument_id) REFERENCES arguments(id) ON DELETE RESTRICT,
            FOREIGN KEY (verdict_id) REFERENCES argument_verdicts(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_argument_target_arguments_target "
        "ON argument_target_arguments(target_argument_id)"
    )
    conn.execute(
        "CREATE INDEX idx_argument_target_positions_position "
        "ON argument_target_positions(position_id)"
    )

    if has_legacy:
        rows = conn.execute(
            "SELECT id, work_id, premise, conclusion, created_at FROM arguments_legacy_v11"
        ).fetchall()
        for row in rows:
            arg_id = str(row[0] if not hasattr(row, "keys") else row["id"])
            work_id = row[1] if not hasattr(row, "keys") else row["work_id"]
            premise = row[2] if not hasattr(row, "keys") else row["premise"]
            conclusion = row[3] if not hasattr(row, "keys") else row["conclusion"]
            created_at = row[4] if not hasattr(row, "keys") else row["created_at"]
            short = arg_id[2:] if arg_id.startswith("A-") else arg_id
            name = "Argument %s" % short
            main_text = _legacy_argument_main_text(premise, conclusion)
            conn.execute(
                """
                INSERT INTO arguments (id, name, kind, main_text, created_at, updated_at)
                VALUES (?, ?, 'argument', ?, ?, ?)
                """,
                (arg_id, name, main_text, created_at, created_at),
            )
            if work_id:
                conn.execute(
                    """
                    INSERT INTO argument_sources (
                        argument_id, work_id, pages, order_index, created_at
                    ) VALUES (?, ?, '', 0, ?)
                    """,
                    (arg_id, work_id, created_at),
                )
        conn.execute("DROP TABLE arguments_legacy_v11")


def _legacy_argument_main_text(premise, conclusion) -> str:
    sections = []
    prem = "" if premise is None else str(premise)
    conc = "" if conclusion is None else str(conclusion)
    if prem.strip():
        sections.append("### Premise\n\n" + prem)
    if conc.strip():
        sections.append("### Conclusion\n\n" + conc)
    return "\n\n".join(sections)


def migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE saved_views (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL CHECK(TRIM(name) <> ''),

            mode TEXT NOT NULL
                CHECK(mode IN ('all', 'advanced', 'tag')),

            search_q TEXT NOT NULL DEFAULT '',
            search_tag TEXT NOT NULL DEFAULT '',
            search_author TEXT NOT NULL DEFAULT '',
            search_publisher TEXT NOT NULL DEFAULT '',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            CHECK (
                (
                    mode = 'all'
                    AND TRIM(search_q) <> ''
                    AND search_tag = ''
                    AND search_author = ''
                    AND search_publisher = ''
                )
                OR
                (
                    mode = 'advanced'
                    AND search_tag = ''
                    AND (
                        TRIM(search_q) <> ''
                        OR TRIM(search_author) <> ''
                        OR TRIM(search_publisher) <> ''
                    )
                )
                OR
                (
                    mode = 'tag'
                    AND TRIM(search_tag) <> ''
                    AND search_q = ''
                )
            )
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_saved_views_name_nocase "
        "ON saved_views(name COLLATE NOCASE)"
    )


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
        if (
            name in _POST_V10_TABLES
            or name in _POST_V11_TABLES
            or name in _POST_V13_TABLES
            or name in _POST_V16_TABLES
        ):
            continue
        if name in _LEGACY_LEAF_TABLE_SQL:
            if not table_exists(conn, name):
                conn.execute(_LEGACY_LEAF_TABLE_SQL[name])
            continue
        if name == "arguments":
            if not table_exists(conn, "arguments"):
                conn.execute(_LEGACY_ARGUMENTS_SQL)
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
        if present != set(required_fks):
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


def _index_key_cids(conn: sqlite3.Connection, name: str) -> Tuple[int, ...]:
    cids: List[int] = []
    for row in conn.execute(f"PRAGMA index_xinfo({_ident(name)})"):
        raw_key = _row_field(row, 5, "key")
        try:
            key = 1 if raw_key is None else int(raw_key)
        except (TypeError, ValueError):
            key = 1
        if key == 0:
            continue
        cids.append(int(_row_field(row, 1, "cid") or 0))
    return tuple(cids)


def _split_create_index_key_exprs(sql: str) -> Optional[List[str]]:
    """Split CREATE INDEX ... ON table (key, ...) into key expressions. Not a general SQL parser."""
    text = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    match = re.search(r"\bON\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*\(", text, re.IGNORECASE)
    if not match:
        return None
    i = match.end()
    depth = 1
    buf: List[str] = []
    parts: List[str] = []
    in_single = False
    n = len(text)
    while i < n and depth > 0:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
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
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                break
            buf.append(ch)
        elif ch == "," and depth == 1:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    else:
        return None
    return parts


def _folders_parent_title_index_matches(conn: sqlite3.Connection, spec: IndexSpec) -> bool:
    """Exact two-key expression contract for idx_folders_parent_title_nocase."""
    key_cids = _index_key_cids(conn, spec.name)
    if key_cids != (-2,) * len(spec.expression_tokens):
        return False
    sql = _master_sql(conn, "index", spec.name) or ""
    exprs = _split_create_index_key_exprs(sql)
    if exprs is None or len(exprs) != len(spec.expression_tokens):
        return False
    actual = tuple(_normalize_index_sql(expr) for expr in exprs)
    expected = tuple(_normalize_index_sql(token) for token in spec.expression_tokens)
    return actual == expected


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
        return _folders_parent_title_index_matches(conn, spec)
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


def migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE sync_operations (
            op_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            http_status INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE sync_entity_revisions (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 0),
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (scope_type, scope_id)
        )
    """)
    conn.execute("""
        CREATE TABLE sync_tag_lifecycle (
            tag_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
            target_tag_id TEXT,
            changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK ((state = 'merged' AND target_tag_id IS NOT NULL) OR
                   (state != 'merged' AND target_tag_id IS NULL))
        )
    """)
    conn.execute("INSERT INTO sync_tag_lifecycle (tag_id, state) SELECT id, 'active' FROM tags")


def migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """Annotation set vs materialized PDF generation tracking (Slice F).

    Also creates ``idx_roles_person_work_role_unique`` for databases that already
    sat at schema 14 when that index was added to INDEX_SPECS / db_schema.sql
    without a version bump (fef456a). Fresh and pre-v10 upgrade paths already
    have it; in-place v14 libraries do not until this step.
    """
    if not column_exists(conn, "works", "canonical_annotation_set_revision"):
        conn.execute(
            "ALTER TABLE works ADD COLUMN canonical_annotation_set_revision "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if not column_exists(conn, "works", "materialized_pdf_annotation_revision"):
        conn.execute(
            "ALTER TABLE works ADD COLUMN materialized_pdf_annotation_revision "
            "INTEGER NOT NULL DEFAULT 0"
        )
    _ensure_roles_person_work_role_unique(conn)


def migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """Durable claims for managed PDFs a committed Work deletion still owes.

    The canonical Work row commits independently of filesystem cleanup, so the
    ``file_path`` that identifies the orphan is gone before ``os.remove()`` is
    attempted. One row per managed basename -- never an absolute path -- is
    written in the same transaction as the Work delete and removed once the
    bytes are gone or another Work references that name. Existing libraries
    start with an empty table: PRKS cannot reconstruct deletions it never
    recorded, and guessing from the pdfs directory could delete a file no Work
    has claimed yet.
    """
    if not table_exists(conn, "pending_pdf_cleanup"):
        conn.execute(
            """
            CREATE TABLE pending_pdf_cleanup (
                filename TEXT PRIMARY KEY,
                recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_attempt_at TIMESTAMP
            )
            """
        )


def _ensure_roles_person_work_role_unique(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "roles"):
        return
    spec = _index_spec("idx_roles_person_work_role_unique")
    if spec is None or _index_matches_spec(conn, spec):
        return
    conflict = conn.execute(
        "SELECT 1 FROM roles GROUP BY person_id, work_id, role_type "
        "HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if conflict is not None:
        raise MigrationError(
            "legacy_constraint_conflict",
            "Legacy database has conflicting rows for a uniqueness constraint.",
            constraint="roles_person_work_role_unique",
        )
    if index_exists(conn, spec.name):
        conn.execute(f"DROP INDEX {_ident(spec.name)}")
    conn.execute(_INDEX_SQL[spec.name])


# ---------------------------------------------------------------------------
# Schema 17: #60 Slice A -- Work -> Manifestation -> Asset identity layer.
# See docs/work-identity-model.md (§4.1, §8.5, §12.2, §12.3 step 1).
#
# FROZEN: this is the v17 shape the migration creates. `db_schema.sql` carries
# the same statements, and `validate_current_schema` compares each object's
# stored SQL against them, so fresh and upgraded libraries cannot drift. A
# later slice that changes one of these objects adds its own migration and
# moves the validator to that shape; it never edits this text.
#
# Authority at schema 17: `works` is the ONLY authority for every field. The
# `legacy_work_*_mirror` views define the projection, the `*_mirror_*`
# triggers keep `manifestations`/`assets` equal to it in the same statement
# (works -> new rows only; nothing writes back), and the `*_mirror_read_only`
# triggers refuse any other write to a mirrored column.
#
# One deliberate deviation from the design text: the pinned-citation FK on
# `argument_sources` is `ON DELETE NO ACTION` (immediate), not `RESTRICT`.
# SQLite applies RESTRICT at the moment the parent row goes, so a whole-Work
# delete -- which cascades both the Manifestation and the citation rows --
# failed or succeeded depending on which child table SQLite happened to
# process first (creation order). Immediate NO ACTION checks at the end of the
# statement: deleting a pinned Version alone is still refused, and deleting the
# whole Work cascades cleanly whatever the table order.
# ---------------------------------------------------------------------------
_V17_WORK_IDENTITY_SQL = """
CREATE TABLE manifestations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    origin_work_id TEXT UNIQUE,
    kind TEXT NOT NULL DEFAULT 'unspecified' CHECK (kind IN ('unspecified', 'preprint', 'accepted_manuscript', 'published', 'edition', 'translation', 'reprint', 'web_page', 'video', 'other')),
    title TEXT CHECK (title IS NULL OR title <> ''),
    subtitle TEXT,
    abstract TEXT CHECK (abstract IS NULL OR abstract <> ''),
    language TEXT,
    doc_type TEXT,
    year TEXT,
    published_date TEXT,
    edition TEXT,
    publisher TEXT,
    location TEXT,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    isbn TEXT,
    doi TEXT,
    url TEXT,
    urldate TEXT,
    primary_asset_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, work_id),
    FOREIGN KEY (primary_asset_id, id) REFERENCES assets(id, manifestation_id)
        ON UPDATE NO ACTION ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    manifestation_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    origin_work_id TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('managed_file', 'external_stream')),
    role TEXT NOT NULL DEFAULT 'document' CHECK (role IN ('document', 'snapshot', 'readable_text', 'attachment', 'other')),
    storage_locator TEXT,
    source_locator TEXT,
    provider TEXT,
    provider_id TEXT,
    url TEXT,
    media_type TEXT,
    byte_size INTEGER,
    ingest_sha256 TEXT,
    content_sha256 TEXT,
    content_generation INTEGER NOT NULL DEFAULT 0,
    fingerprinted_at TIMESTAMP,
    origin TEXT NOT NULL CHECK (origin IN ('upload', 'processing_import', 'adopted', 'web_capture', 'legacy')),
    origin_url TEXT,
    origin_ref TEXT,
    derived_from_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    supersedes_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'trashed')),
    thumb_page INTEGER,
    thumb_url TEXT,
    canonical_annotation_set_revision INTEGER NOT NULL DEFAULT 0,
    materialized_pdf_annotation_revision INTEGER NOT NULL DEFAULT 0,
    captured_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, manifestation_id),
    UNIQUE (id, work_id),
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE manifestation_relations (
    work_id TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('revision_of', 'published_version_of', 'new_edition_of', 'translation_of', 'reprint_of')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (from_id, to_id, relation),
    CHECK (from_id <> to_id),
    FOREIGN KEY (from_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (to_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE manifestation_identifiers (
    manifestation_id TEXT NOT NULL REFERENCES manifestations(id) ON DELETE CASCADE,
    scheme TEXT NOT NULL CHECK (length(scheme) > 0),
    value TEXT NOT NULL,
    normalized TEXT NOT NULL CHECK (length(normalized) > 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (manifestation_id, scheme, normalized)
);

CREATE TABLE sync_work_lifecycle (
    work_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
    target_work_id TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((state = 'merged' AND target_work_id IS NOT NULL) OR
           (state != 'merged' AND target_work_id IS NULL))
);

CREATE TABLE work_retirement_guard (
    id INTEGER PRIMARY KEY CHECK (0)
);

CREATE TABLE work_retirement (
    work_id TEXT PRIMARY KEY,
    must_clear INTEGER NOT NULL DEFAULT 1
        REFERENCES work_retirement_guard(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE legacy_inferred_video_urls (
    work_id TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL
);

CREATE TABLE migration_quarantine (
    id INTEGER PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_rowid INTEGER,
    row_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    quarantined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE annotations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    type TEXT,
    content TEXT,
    page_index INTEGER,
    color TEXT,
    geometry_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    asset_id TEXT,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, work_id) REFERENCES assets(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE roles (
    person_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    credit_name TEXT,
    manifestation_id TEXT,
    PRIMARY KEY (person_id, work_id, role_type, order_index),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE argument_sources (
    argument_id TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    manifestation_id TEXT,
    pages TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, order_index),
    FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE NO ACTION
);

CREATE INDEX idx_manifestations_work_id ON manifestations(work_id);
CREATE INDEX idx_manifestations_primary_asset ON manifestations(primary_asset_id, id);
CREATE INDEX idx_assets_manifestation ON assets(manifestation_id, work_id);
CREATE INDEX idx_assets_derived_from ON assets(derived_from_asset_id);
CREATE INDEX idx_assets_supersedes ON assets(supersedes_asset_id);
CREATE INDEX idx_manifestation_relations_from ON manifestation_relations(from_id, work_id);
CREATE INDEX idx_manifestation_relations_to ON manifestation_relations(to_id, work_id);
CREATE INDEX idx_manifestation_identifiers_lookup ON manifestation_identifiers(scheme, normalized);
CREATE INDEX idx_annotations_asset ON annotations(asset_id, work_id);
CREATE INDEX idx_roles_manifestation ON roles(manifestation_id, work_id);
CREATE INDEX idx_argument_sources_manifestation ON argument_sources(manifestation_id, work_id);
CREATE UNIQUE INDEX idx_argument_sources_citation ON argument_sources(argument_id, work_id, COALESCE(manifestation_id, ''), pages);

CREATE VIEW legacy_work_asset_mirror AS
SELECT
    s.id AS work_id,
    (COALESCE(s.file_path, '') <> ''
     OR s.kind_norm = 'video'
     OR COALESCE(s.provider, '') <> ''
     OR COALESCE(s.provider_id, '') <> ''
     OR COALESCE(s.source_mime, '') <> ''
     OR COALESCE(s.thumb_url, '') <> ''
     OR s.thumb_page IS NOT NULL
     OR COALESCE(s.canonical_annotation_set_revision, 0) <> 0
     OR COALESCE(s.materialized_pdf_annotation_revision, 0) <> 0
     OR s.is_stream) AS has_asset_value,
    CASE WHEN s.is_stream THEN 'external_stream' ELSE 'managed_file' END AS kind,
    CASE WHEN s.is_stream THEN NULL ELSE s.locator END AS storage_locator,
    s.provider AS provider,
    s.provider_id AS provider_id,
    CASE WHEN s.is_stream THEN s.source_url END AS url,
    CASE
        WHEN COALESCE(s.source_mime, '') <> '' THEN s.source_mime
        WHEN NOT s.is_stream AND s.locator IS NOT NULL THEN 'application/pdf'
    END AS media_type,
    s.thumb_page AS thumb_page,
    s.thumb_url AS thumb_url,
    s.canonical_annotation_set_revision AS canonical_annotation_set_revision,
    s.materialized_pdf_annotation_revision AS materialized_pdf_annotation_revision
FROM (
    SELECT
        w.id, w.file_path, w.source_url, w.source_mime, w.thumb_url, w.thumb_page,
        w.provider, w.provider_id, w.canonical_annotation_set_revision,
        w.materialized_pdf_annotation_revision,
        lower(trim(COALESCE(w.source_kind, ''), char(32, 9, 10, 11, 12, 13))) AS kind_norm,
        CASE lower(trim(COALESCE(w.source_kind, ''), char(32, 9, 10, 11, 12, 13)))
            WHEN 'video' THEN 1
            WHEN 'pdf' THEN 0
            ELSE (trim(COALESCE(w.file_path, ''), char(32, 9, 10, 11, 12, 13)) = ''
                  AND trim(COALESCE(w.source_url, ''), char(32, 9, 10, 11, 12, 13)) <> ''
                  AND (COALESCE(w.provider, '') <> ''
                       OR COALESCE(w.provider_id, '') <> ''
                       OR EXISTS (SELECT 1 FROM legacy_inferred_video_urls u
                                  WHERE u.work_id = w.id AND u.source_url = w.source_url)))
        END AS is_stream,
        CASE
            WHEN substr(w.file_path, 1, 10) = '/api/pdfs/'
             AND length(w.file_path) > 10
             AND instr(substr(w.file_path, 11), '/') = 0
             AND instr(substr(w.file_path, 11), char(92)) = 0
             AND substr(w.file_path, 11) NOT IN ('.', '..')
             AND substr(w.file_path, 11) = trim(substr(w.file_path, 11), char(32, 9, 10, 11, 12, 13))
             AND NOT (substr(w.file_path, 11) GLOB '*%[0-9A-Fa-f][0-9A-Fa-f]*')
            THEN substr(w.file_path, 11)
        END AS locator
    FROM works w
) s;

CREATE VIEW legacy_work_manifestation_mirror AS
SELECT
    w.id AS work_id,
    w.doc_type AS doc_type,
    w.year AS year,
    w.published_date AS published_date,
    w.edition AS edition,
    w.publisher AS publisher,
    w.location AS location,
    w.journal AS journal,
    w.volume AS volume,
    w.issue AS issue,
    w.pages AS pages,
    w.isbn AS isbn,
    w.doi AS doi,
    CASE
        WHEN a.kind = 'external_stream' THEN NULL
        ELSE w.source_url
    END AS url,
    w.urldate AS urldate
FROM works w
LEFT JOIN assets a ON a.origin_work_id = w.id AND a.work_id = w.id;

CREATE TRIGGER works_au
AFTER UPDATE OF title, abstract, text_content, author_text ON works
BEGIN
  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
  INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
  VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
END;

CREATE TRIGGER works_manifestation_pointers_insert
BEFORE INSERT ON works
WHEN NEW.primary_manifestation_id IS NOT NULL OR NEW.citation_manifestation_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'WORK_POINTERS_START_NULL');
END;

CREATE TRIGGER works_manifestation_pointers_owned
BEFORE UPDATE OF primary_manifestation_id, citation_manifestation_id ON works
BEGIN
    SELECT RAISE(ABORT, 'WORK_PRIMARY_MANIFESTATION_REQUIRED')
    WHERE NEW.primary_manifestation_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM work_retirement r WHERE r.work_id = NEW.id);
    SELECT RAISE(ABORT, 'MANIFESTATION_OWNER_MISMATCH')
    WHERE NEW.primary_manifestation_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM manifestations m
                      WHERE m.id = NEW.primary_manifestation_id AND m.work_id = NEW.id);
    SELECT RAISE(ABORT, 'MANIFESTATION_OWNER_MISMATCH')
    WHERE NEW.citation_manifestation_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM manifestations m
                      WHERE m.id = NEW.citation_manifestation_id AND m.work_id = NEW.id);
END;

CREATE TRIGGER works_retirement_clear
AFTER DELETE ON works
BEGIN
    DELETE FROM work_retirement WHERE work_id = OLD.id;
END;

CREATE TRIGGER work_retirement_delete_only_after_work
BEFORE DELETE ON work_retirement
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_WORK_STILL_EXISTS')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id);
END;

CREATE TRIGGER work_retirement_no_update
BEFORE UPDATE ON work_retirement
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_IMMUTABLE');
END;

CREATE TRIGGER work_retirement_guard_no_update
BEFORE UPDATE ON work_retirement_guard
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_IMMUTABLE');
END;

CREATE TRIGGER manifestations_pointer_target_move
BEFORE UPDATE OF id, work_id ON manifestations
WHEN NEW.id IS NOT OLD.id OR NEW.work_id IS NOT OLD.work_id
BEGIN
    SELECT RAISE(ABORT, 'MANIFESTATION_IS_POINTER_TARGET')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id
                  AND (w.primary_manifestation_id = OLD.id OR w.citation_manifestation_id = OLD.id));
END;

CREATE TRIGGER manifestations_pointer_target_delete
BEFORE DELETE ON manifestations
BEGIN
    SELECT RAISE(ABORT, 'MANIFESTATION_IS_POINTER_TARGET')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id
                  AND (w.primary_manifestation_id = OLD.id OR w.citation_manifestation_id = OLD.id));
END;

CREATE TRIGGER manifestation_origin_immutable
BEFORE UPDATE OF origin_work_id ON manifestations
WHEN NEW.origin_work_id IS NOT OLD.origin_work_id
BEGIN
    SELECT RAISE(ABORT, 'ORIGIN_WORK_IMMUTABLE');
END;

CREATE TRIGGER asset_origin_immutable
BEFORE UPDATE OF origin_work_id ON assets
WHEN NEW.origin_work_id IS NOT OLD.origin_work_id
BEGIN
    SELECT RAISE(ABORT, 'ORIGIN_WORK_IMMUTABLE');
END;

CREATE TRIGGER manifestations_mirror_read_only
BEFORE UPDATE OF doc_type, year, published_date, edition, publisher, location, journal, volume, issue, pages, isbn, doi, url, urldate ON manifestations
WHEN NEW.origin_work_id IS NOT NULL AND NEW.work_id = NEW.origin_work_id
 AND (NEW.doc_type, NEW.year, NEW.published_date, NEW.edition, NEW.publisher, NEW.location,
      NEW.journal, NEW.volume, NEW.issue, NEW.pages, NEW.isbn, NEW.doi, NEW.url, NEW.urldate)
     IS NOT (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location,
                    v.journal, v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate
             FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.work_id)
BEGIN
    SELECT RAISE(ABORT, 'MIRRORED_FIELD_READ_ONLY');
END;

CREATE TRIGGER assets_mirror_read_only
BEFORE UPDATE OF kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url, canonical_annotation_set_revision, materialized_pdf_annotation_revision ON assets
WHEN NEW.origin_work_id IS NOT NULL AND NEW.work_id = NEW.origin_work_id
 AND (NEW.kind, NEW.storage_locator, NEW.provider, NEW.provider_id, NEW.url, NEW.media_type,
      NEW.thumb_page, NEW.thumb_url, NEW.canonical_annotation_set_revision,
      NEW.materialized_pdf_annotation_revision)
     IS NOT (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                    v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                    v.materialized_pdf_annotation_revision
             FROM legacy_work_asset_mirror v WHERE v.work_id = NEW.work_id)
BEGIN
    SELECT RAISE(ABORT, 'MIRRORED_FIELD_READ_ONLY');
END;

CREATE TRIGGER works_mirror_ai
AFTER INSERT ON works
BEGIN
    INSERT INTO manifestations (id, work_id, origin_work_id, doc_type, year, published_date, edition,
                                publisher, location, journal, volume, issue, pages, isbn, doi, url, urldate)
    SELECT 'MF-' || hex(randomblob(16)), v.work_id, v.work_id, v.doc_type, v.year, v.published_date,
           v.edition, v.publisher, v.location, v.journal, v.volume, v.issue, v.pages, v.isbn, v.doi,
           v.url, v.urldate
    FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id;
    UPDATE works SET primary_manifestation_id =
        (SELECT m.id FROM manifestations m WHERE m.origin_work_id = NEW.id)
    WHERE id = NEW.id;
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.id);
END;

CREATE TRIGGER works_mirror_manifestation_au
AFTER UPDATE OF doc_type, year, published_date, edition, publisher, location, journal, volume, issue, pages, isbn, doi, urldate, source_url ON works
BEGIN
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
END;

CREATE TRIGGER works_mirror_asset_au
AFTER UPDATE OF file_path, source_kind, source_url, source_mime, thumb_url, thumb_page, provider, provider_id, canonical_annotation_set_revision, materialized_pdf_annotation_revision ON works
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.id);
    UPDATE assets
    SET (kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url,
         canonical_annotation_set_revision, materialized_pdf_annotation_revision, updated_at) =
        (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                v.materialized_pdf_annotation_revision, CURRENT_TIMESTAMP
         FROM legacy_work_asset_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
END;

CREATE TRIGGER assets_mirror_ai
AFTER INSERT ON assets
WHEN NEW.origin_work_id IS NOT NULL AND NEW.origin_work_id = NEW.work_id
BEGIN
    UPDATE manifestations SET primary_asset_id = NEW.id
    WHERE id = NEW.manifestation_id AND primary_asset_id IS NULL;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.work_id)
    WHERE origin_work_id = NEW.work_id AND work_id = NEW.work_id;
END;

CREATE TRIGGER annotations_mirror_asset_ai
AFTER INSERT ON annotations
WHEN NEW.asset_id IS NULL
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.work_id
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.work_id);
    UPDATE annotations SET asset_id =
        (SELECT a.id FROM assets a WHERE a.origin_work_id = NEW.work_id AND a.work_id = NEW.work_id)
    WHERE id = NEW.id;
END;

CREATE TRIGGER sync_revisions_mirror_asset_ai
AFTER INSERT ON sync_entity_revisions
WHEN NEW.scope_type IN ('work-source', 'pdf-annotation', 'work-field')
 AND CASE WHEN json_valid(NEW.scope_id) AND json_type(NEW.scope_id) = 'array'
          THEN json_type(NEW.scope_id, '$[0]') IS 'text'
           AND CASE NEW.scope_type
                   WHEN 'work-source' THEN json_array_length(NEW.scope_id) = 1
                   WHEN 'pdf-annotation' THEN json_array_length(NEW.scope_id) = 2
                        AND json_type(NEW.scope_id, '$[1]') IS 'text'
                   ELSE json_array_length(NEW.scope_id) = 2
                        AND json_type(NEW.scope_id, '$[1]') IS 'text'
                        AND json_extract(NEW.scope_id, '$[1]') IS 'thumb_page'
               END
          ELSE 0
     END
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = json_extract(NEW.scope_id, '$[0]')
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = v.work_id);
END;

CREATE TRIGGER legacy_inferred_video_urls_ai
AFTER INSERT ON legacy_inferred_video_urls
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.work_id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.work_id);
    UPDATE assets
    SET (kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url,
         canonical_annotation_set_revision, materialized_pdf_annotation_revision, updated_at) =
        (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                v.materialized_pdf_annotation_revision, CURRENT_TIMESTAMP
         FROM legacy_work_asset_mirror v WHERE v.work_id = NEW.work_id)
    WHERE origin_work_id = NEW.work_id AND work_id = NEW.work_id;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.work_id)
    WHERE origin_work_id = NEW.work_id AND work_id = NEW.work_id;
END;

CREATE TRIGGER legacy_inferred_video_urls_ad
AFTER DELETE ON legacy_inferred_video_urls
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = OLD.work_id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = OLD.work_id);
    UPDATE assets
    SET (kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url,
         canonical_annotation_set_revision, materialized_pdf_annotation_revision, updated_at) =
        (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                v.materialized_pdf_annotation_revision, CURRENT_TIMESTAMP
         FROM legacy_work_asset_mirror v WHERE v.work_id = OLD.work_id)
    WHERE origin_work_id = OLD.work_id AND work_id = OLD.work_id;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = OLD.work_id)
    WHERE origin_work_id = OLD.work_id AND work_id = OLD.work_id;
END;

CREATE TRIGGER argument_sources_mirror_pin_ai
AFTER INSERT ON argument_sources
WHEN NEW.manifestation_id IS NULL AND NEW.pages <> ''
BEGIN
    UPDATE argument_sources
    SET manifestation_id = (SELECT w.primary_manifestation_id FROM works w WHERE w.id = NEW.work_id)
    WHERE argument_id = NEW.argument_id AND order_index = NEW.order_index;
END;
"""

# The three leaf tables v17 rebuilds (create new, copy, drop old, rename).
_V17_REBUILT_TABLES = ("annotations", "roles", "argument_sources")
# Existing indexes on the rebuilt tables, recreated after the rebuild.
_V17_RECREATED_INDEXES = (
    "idx_annotations_work_id",
    "idx_roles_work_id",
    "idx_roles_person_id",
    "idx_roles_person_work_role_unique",
    "idx_argument_sources_work_id",
)


def _v17_objects() -> List[Tuple[str, str, str]]:
    """(kind, name, sql) for every Slice A object, in creation order."""
    out: List[Tuple[str, str, str]] = []
    for stmt in iter_sql_statements(_V17_WORK_IDENTITY_SQL):
        compact = _leading_sql(stmt)
        kind, name = _classify_ddl(compact)
        if kind == "other":
            match = re.match(r"^CREATE\s+VIEW\s+([A-Za-z_][A-Za-z0-9_]*)", compact, re.IGNORECASE)
            if match:
                kind, name = "view", match.group(1)
        if kind not in ("table", "index", "trigger", "view") or name is None:
            raise MigrationError("internal", "Unexpected statement in the v17 schema.")
        out.append((kind, name, compact))
    return out


def _normalize_ddl(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", text, flags=re.IGNORECASE)
    text = text.replace('"', "").replace("`", "")
    return re.sub(r"\s+", "", text).upper().rstrip(";")


def _validate_work_identity_objects(conn: sqlite3.Connection) -> None:
    """Every Slice A table, index, view and trigger, compared by definition.

    Column and FK-tuple checks alone cannot see what these objects exist for:
    `DEFERRABLE INITIALLY DEFERRED`, the guard's `CHECK (0)`, the self-relation
    CHECK, composite FK column pairing, and every trigger body. So the stored
    SQL of each is compared with the canonical statement.
    """
    for kind, name, sql in _v17_objects():
        stored = _master_sql(conn, kind, name)
        if stored is None:
            raise MigrationError("schema_drift", "Required schema object is missing.", object=name)
        if _normalize_ddl(stored) != _normalize_ddl(sql):
            raise MigrationError(
                "schema_drift",
                "Required schema object is missing or has the wrong definition.",
                object=name,
            )


def _fk_violations(conn: sqlite3.Connection) -> set[Tuple[str, int, str]]:
    found: set[Tuple[str, int, str]] = set()
    for row in conn.execute("PRAGMA foreign_key_check").fetchall():
        found.add((str(row[0]), row[1], str(row[2])))
    return found


def _advance_revision(conn: sqlite3.Connection, scope_type: str, scope_id: str) -> None:
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id),
    )


def _v17_quarantine(conn: sqlite3.Connection, table: str) -> int:
    """Move a leaf table's pre-existing FK orphans into `migration_quarantine`.

    The rebuilt table keeps its immediate single-column FKs, so these rows
    cannot be copied. They are kept verbatim instead of dropped, and every live
    aggregate whose state endpoint reported them advances its revision, so the
    absence is strictly newer than the presence a device may hold (§12.3 1.1).
    """
    from backend import argument_sync, work_role_sync

    parents: Dict[int, set] = defaultdict(set)
    for row in conn.execute(f"PRAGMA foreign_key_check({_ident(table)})").fetchall():
        parents[int(row[1])].add(str(row[2]))
    if not parents:
        return 0
    columns = _table_column_names(conn, table)
    json_args = ", ".join("'%s', %s" % (c, _ident(c)) for c in columns)
    advanced_arguments: set = set()
    for rowid in sorted(parents):
        row = conn.execute(
            f"SELECT json_object({json_args}) FROM {_ident(table)} WHERE rowid = ?", (rowid,)
        ).fetchone()
        conn.execute(
            "INSERT INTO migration_quarantine (source_table, source_rowid, row_json, reason) "
            "VALUES (?, ?, ?, ?)",
            (table, rowid, row[0], "missing_parent:" + ",".join(sorted(parents[rowid]))),
        )
        data = json.loads(row[0])
        if table == "roles":
            live_work = conn.execute(
                "SELECT 1 FROM works WHERE id = ?", (data.get("work_id"),)
            ).fetchone()
            if live_work is not None:
                _advance_revision(
                    conn,
                    work_role_sync.SCOPE_TYPE,
                    work_role_sync.scope_key(data["work_id"], data["person_id"], data["role_type"]),
                )
        elif table == "argument_sources":
            argument_id = data.get("argument_id")
            live_argument = conn.execute(
                "SELECT 1 FROM arguments WHERE id = ?", (argument_id,)
            ).fetchone()
            if live_argument is not None and argument_id not in advanced_arguments:
                advanced_arguments.add(argument_id)
                _advance_revision(conn, argument_sync.SOURCES_SCOPE_TYPE, argument_id)
        # annotations: only a missing Work can orphan one, so no live scope
        # ever reported it and none is created.
    LOGGER.info("db_migration_quarantine table=%s rows=%s", safe_log_label(table), len(parents))
    return len(parents)


def _v17_backfill(conn: sqlite3.Connection) -> None:
    """Deterministic Work -> 1 Manifestation -> 0..1 Asset (§12.2)."""
    from backend import work_identity

    mirror_cols = work_identity.MANIFESTATION_MIRROR_COLUMNS
    work_ids = [r[0] for r in conn.execute("SELECT id FROM works ORDER BY id").fetchall()]
    insert_mf = (
        "INSERT INTO manifestations (id, work_id, origin_work_id, %s, created_at, updated_at) "
        "SELECT ?, v.work_id, v.work_id, %s, w.created_at, w.updated_at "
        "FROM legacy_work_manifestation_mirror v JOIN works w ON w.id = v.work_id "
        "WHERE v.work_id = ?"
        % (", ".join(mirror_cols), ", ".join("v." + c for c in mirror_cols))
    )
    for work_id in work_ids:
        conn.execute(insert_mf, (work_identity.backfill_manifestation_id(work_id), work_id))
    conn.execute(
        "UPDATE works SET primary_manifestation_id = "
        "(SELECT m.id FROM manifestations m WHERE m.origin_work_id = works.id)"
    )

    # The parser verdicts first: they decide stream vs citation URL in the view.
    work_identity.refresh_all_inferred_video_urls(conn)
    needs_asset = work_identity.works_requiring_origin_asset(conn)
    for work_id in sorted(needs_asset):
        if work_identity.ensure_origin_asset(conn, work_id) is None:
            raise MigrationError("backfill_unmapped", "A Work could not be backfilled.")


def _v17_rebuild_leaf_tables(conn: sqlite3.Connection, ddl: Dict[str, str]) -> None:
    def create_new(table: str) -> str:
        new_name = table + "_v17_rebuild"
        sql, count = re.subn(
            r"^CREATE\s+TABLE\s+%s\s*\(" % table, "CREATE TABLE %s (" % new_name, ddl[table], count=1
        )
        if count != 1:
            raise MigrationError("internal", "Unexpected v17 table definition.", object=table)
        conn.execute(sql)
        return new_name

    def swap(table: str, new_name: str) -> None:
        conn.execute(f"DROP TABLE {_ident(table)}")
        conn.execute(f"ALTER TABLE {_ident(new_name)} RENAME TO {_ident(table)}")

    skip = "rowid NOT IN (SELECT source_rowid FROM migration_quarantine WHERE source_table = ?)"

    # annotations: same rows and rowids, each bound to its Work's origin Asset.
    new_name = create_new("annotations")
    common = [c for c in _table_column_names(conn, "annotations") if c in set(
        ("id", "work_id", "type", "content", "page_index", "color", "geometry_json",
         "created_at", "updated_at"))]
    cols = ", ".join(common)
    conn.execute(
        f"INSERT INTO {new_name} (rowid, {cols}, asset_id) "
        f"SELECT t.rowid, {', '.join('t.' + c for c in common)}, "
        "(SELECT a.id FROM assets a WHERE a.origin_work_id = t.work_id AND a.work_id = t.work_id) "
        f"FROM annotations t WHERE t.{skip}",
        ("annotations",),
    )
    swap("annotations", new_name)

    # roles: same rows and rowids (readers order ties by rowid); every role
    # stays Work-scoped until the role-scope slice (§7.3).
    new_name = create_new("roles")
    common = [c for c in _table_column_names(conn, "roles") if c in set(
        ("person_id", "work_id", "role_type", "order_index", "credit_name"))]
    cols = ", ".join(common)
    conn.execute(
        f"INSERT INTO {new_name} (rowid, {cols}) SELECT rowid, {cols} FROM roles WHERE {skip}",
        ("roles",),
    )
    swap("roles", new_name)

    # argument_sources: copied in the canonical read order (order_index,
    # work_id), renumbered 0..n-1, rows with a pinpoint pinned to the Work's
    # backfilled Manifestation (§8.5).
    new_name = create_new("argument_sources")
    rows = conn.execute(
        "SELECT s.argument_id, s.work_id, s.pages, s.created_at, w.primary_manifestation_id "
        "FROM argument_sources s LEFT JOIN works w ON w.id = s.work_id "
        f"WHERE s.{skip} ORDER BY s.argument_id, s.order_index, s.work_id",
        ("argument_sources",),
    ).fetchall()
    position: Dict[str, int] = defaultdict(int)
    for argument_id, work_id, pages, created_at, manifestation_id in rows:
        pages = "" if pages is None else pages
        order = position[argument_id]
        position[argument_id] += 1
        conn.execute(
            f"INSERT INTO {new_name} (argument_id, order_index, work_id, manifestation_id, "
            "pages, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (argument_id, order, work_id, manifestation_id if pages != "" else None,
             pages, created_at),
        )
    swap("argument_sources", new_name)


def _v17_sql(objects: List[Tuple[str, str, str]], kind: str) -> Dict[str, str]:
    return {name: sql for obj_kind, name, sql in objects if obj_kind == kind}


def _v17_create_entities(conn: sqlite3.Connection, objects: List[Tuple[str, str, str]]) -> None:
    """New entities, the two Work pointers, the scoped FTS trigger, the views."""
    for name, sql in _v17_sql(objects, "table").items():
        if name not in _V17_REBUILT_TABLES and name != "migration_quarantine":
            conn.execute(sql)
    # The FTS update trigger becomes column-scoped. Unscoped, it re-indexed a
    # row on every UPDATE -- including the pointer write the Work-insert mirror
    # makes, which can run before `works_ai` has indexed the new row and would
    # then delete an FTS entry that does not exist yet.
    conn.execute("DROP TRIGGER IF EXISTS works_au")
    conn.execute(_v17_sql(objects, "trigger")["works_au"])
    conn.execute("ALTER TABLE works ADD COLUMN primary_manifestation_id TEXT")
    conn.execute("ALTER TABLE works ADD COLUMN citation_manifestation_id TEXT")
    for sql in _v17_sql(objects, "view").values():
        conn.execute(sql)


def _v17_install_indexes_and_triggers(
    conn: sqlite3.Connection, objects: List[Tuple[str, str, str]]
) -> None:
    for name in _V17_RECREATED_INDEXES:
        conn.execute(_INDEX_SQL[name])
    for sql in _v17_sql(objects, "index").values():
        conn.execute(sql)
    for name, sql in _v17_sql(objects, "trigger").items():
        if name != "works_au":
            conn.execute(sql)


def _v17_verify(
    conn: sqlite3.Connection,
    objects: List[Tuple[str, str, str]],
    before: set[Tuple[str, int, str]],
) -> None:
    """Nothing may be left that the preflight did not see (§12.3 step 7)."""
    from backend import work_identity

    slice_tables = set(_v17_sql(objects, "table"))
    for violation in _fk_violations(conn):
        table = violation[0]
        if table in slice_tables or violation not in before:
            raise MigrationError(
                "unmapped_legacy_row",
                "The work identity migration left a foreign key violation.",
                object=table,
            )
    if work_identity.integrity_violations(conn):
        raise MigrationError("integrity_violation", "Work identity integrity check failed.")
    if work_identity.mirror_drift(conn):
        raise MigrationError("integrity_violation", "Work identity mirror check failed.")


def migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """#60 Slice A: Work -> Manifestation -> Asset entities and integrity layer.

    SQLite only: no managed file is read, written, listed or hashed, and no
    backup is made (I10, D2). One transaction (the runner's), so any failed
    check below rolls the library back to schema 16 untouched.
    """
    objects = _v17_objects()
    ddl = _v17_sql(objects, "table")
    before = _fk_violations(conn)

    # 1. Preflight and quarantine of rows that already violate a leaf FK.
    conn.execute(ddl["migration_quarantine"])
    for table in _V17_REBUILT_TABLES:
        _v17_quarantine(conn, table)
    # 2-3. New entities, the Work pointers, the projection views.
    _v17_create_entities(conn, objects)
    # 5 before 4: the rebuilt leaf tables copy the backfilled IDs.
    _v17_backfill(conn)
    _v17_rebuild_leaf_tables(conn, ddl)
    # 6. Integrity and mirror triggers, installed after the backfill.
    _v17_install_indexes_and_triggers(conn, objects)
    # 7.
    _v17_verify(conn, objects, before)

MIGRATIONS: Tuple[Migration, ...] = (
    Migration(
        target_version=10,
        name="ordered_migration_baseline",
        apply=migrate_v9_to_v10,
    ),
    Migration(
        target_version=11,
        name="add_saved_views",
        apply=migrate_v10_to_v11,
    ),
    Migration(
        target_version=12,
        name="research_network",
        apply=migrate_v11_to_v12,
    ),
    Migration(
        target_version=13,
        name="remove_legacy_work_annotations",
        apply=migrate_v12_to_v13,
    ),
    Migration(target_version=14, name="work_tag_sync", apply=migrate_v13_to_v14),
    Migration(
        target_version=15,
        name="pdf_annotation_materialization",
        apply=migrate_v14_to_v15,
    ),
    Migration(
        target_version=16,
        name="pending_pdf_cleanup",
        apply=migrate_v15_to_v16,
    ),
    Migration(
        target_version=17,
        name="work_identity_slice_a",
        apply=migrate_v16_to_v17,
    ),
)

validate_migration_registry()
