"""The format-2 archive layout shared by export, R0 and staging (L-B-1 #639; §11.4)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

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
