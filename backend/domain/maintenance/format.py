"""The format-2 archive layout shared by export, R0 and staging (L-B-1 #639; §11.4)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from psycopg import sql

from backend.db.schema_snapshot import catalog_snapshot
from backend.domain.maintenance import tables
from backend.domain.maintenance.connection import pin_interchange_settings

FORMAT_ID = "prks-backup"
FORMAT_VERSION = tables.FORMAT_VERSION
BACKUP_EXTENSION = ".prks-backup"
MANIFEST_NAME = "manifest.json"
USERS_MEMBER = "identities/users.csv"
DEVICES_MEMBER = "identities/devices.csv"
IO_CHUNK_SIZE = 64 * 1024


def table_member(table: str) -> str:
    """``db/works.csv`` for ``prks.works``."""
    schema, name = tables.split_name(table)
    if schema != "prks":
        raise ValueError(f"{table} is not exported")
    return f"db/{name}.csv"


def schema_fingerprint(conn: psycopg.Connection[Any]) -> str:
    """SHA-256 of the normalized catalog (§4.4), the same data the schema gate compares.

    The catalog prints expressions (column defaults, checks) through the
    session's settings, ``IntervalStyle`` among them, so it is read with the
    interchange settings pinned: export and R0 then hash the same text.
    """
    with conn.transaction():
        pin_interchange_settings(conn)
        snapshot = catalog_snapshot(conn)
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def object_references(schema: str = "prks") -> sql.Composed:
    """Every object a Library's rows name, with what the catalogue recorded about it.

    Export reads it from ``prks`` to choose the archive's objects; staging reads
    it from ``restore_<run>`` to require that the archive carries exactly those.
    Bound parameter: ``library_id``.
    """
    return sql.SQL(
        "SELECT 'asset-objects' AS namespace, storage_locator AS name, content_sha256 AS sha256, "
        "byte_size AS size FROM {assets} WHERE library_id = %(library_id)s AND storage_locator IS NOT NULL "
        "UNION ALL "
        "SELECT 'asset-objects', source_locator, ingest_sha256, NULL "
        "FROM {assets} WHERE library_id = %(library_id)s AND source_locator IS NOT NULL "
        "UNION ALL "
        "SELECT 'asset-objects', object_locator, sha256, byte_size "
        "FROM {items} WHERE library_id = %(library_id)s AND object_locator IS NOT NULL "
        "UNION ALL "
        "SELECT 'portraits', portrait_locator, portrait_sha256, portrait_byte_size "
        "FROM {persons} WHERE library_id = %(library_id)s AND portrait_locator IS NOT NULL"
    ).format(assets=sql.Identifier(schema, "assets"), items=sql.Identifier(schema, "processing_items"),
             persons=sql.Identifier(schema, "persons"))
