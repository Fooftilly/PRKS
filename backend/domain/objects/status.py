"""Object counts for the storage status (storage-architecture §11.1).

Counts only, never key names: a minted name embeds a sanitized original
filename, so names are private data (``log_safety``).

- ``missing``: keys a catalogue row names whose object the store does not
  have. Every referenced key is stat-ed, once per status read; the read is
  the owner's, on demand, never scheduled. An absent key is asked again
  *after* its stat: a release and settle between the first read and the
  stat removes the bytes of a key nothing names any more, and that is not
  a lost file. A key whose references cannot be told stays missing.
- ``pending_cleanup``: claims a Library recorded when it stopped naming a
  key, waiting for ``objects.cleanup`` (§11.2).
- ``orphans``: claims the orphan sweep recorded for objects nothing ever
  adopted, waiting for the same settle.
- ``held``: claims a restore holds (``hold``); never settled automatically.

The counts cover the whole instance: one data root serves every Library
(postgresql-architecture §6.2).

``server_locator`` names the database as the server reports it: libpq fills
options a connection string leaves out, so the configuration alone may not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString, Optional

from backend.db.database import Database, Transaction
from backend.domain.objects.lifecycle import references
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    NAMESPACE_REMOTE_IMAGES,
    InvalidStorageKey,
    StorageBackend,
    StorageKey,
    StorageObjectError,
)

_REFERENCED_KEYS: LiteralString = """
SELECT %(assets)s::text AS namespace, storage_locator AS name FROM prks.assets WHERE storage_locator IS NOT NULL
UNION
SELECT %(assets)s::text, source_locator FROM prks.assets WHERE source_locator IS NOT NULL
UNION
SELECT %(assets)s::text, object_locator FROM prks.processing_items WHERE object_locator IS NOT NULL
UNION
SELECT %(portraits)s::text, portrait_locator FROM prks.persons WHERE portrait_locator IS NOT NULL
UNION
SELECT %(remote)s::text, locator FROM prks.remote_images
"""

_CLAIM_COUNTS: LiteralString = """
SELECT
  count(*) FILTER (WHERE NOT hold AND library_id IS NOT NULL) AS pending_cleanup,
  count(*) FILTER (WHERE NOT hold AND library_id IS NULL) AS orphans,
  count(*) FILTER (WHERE hold) AS held
FROM prks.object_cleanup_claims
"""


_SERVER_LOCATOR: LiteralString = """
SELECT host(inet_server_addr()) AS host, inet_server_port() AS port, current_database() AS name
"""


@dataclass(frozen=True)
class ServerLocator:
    """Where the pool is connected; ``host`` is None over a Unix socket."""

    host: Optional[str]
    port: Optional[int]
    name: Optional[str]


def server_locator(db: Database) -> ServerLocator:
    row = db.query(lambda tx: tx.execute(_SERVER_LOCATOR).fetchone())
    if row is None:
        return ServerLocator(None, None, None)
    return ServerLocator(
        host=row["host"] if isinstance(row["host"], str) and row["host"] else None,
        port=int(row["port"]) if row["port"] is not None else None,
        name=row["name"] if isinstance(row["name"], str) and row["name"] else None,
    )


@dataclass(frozen=True)
class ObjectCounts:
    referenced: int
    missing: int
    pending_cleanup: int
    orphans: int
    held: int


def _read(tx: Transaction) -> tuple[list[tuple[str, str]], dict[str, int]]:
    params = {
        "assets": NAMESPACE_ASSET_OBJECTS,
        "portraits": NAMESPACE_PORTRAITS,
        "remote": NAMESPACE_REMOTE_IMAGES,
    }
    keys = [(row["namespace"], row["name"]) for row in tx.execute(_REFERENCED_KEYS, params).fetchall()]
    row = tx.execute(_CLAIM_COUNTS).fetchone()
    claims = {name: int(row[name]) if row is not None else 0 for name in ("pending_cleanup", "orphans", "held")}
    return keys, claims


def object_counts(db: Database, storage: StorageBackend) -> ObjectCounts:
    """Count missing referenced objects and pending claims across the instance."""
    keys, claims = db.query(_read)
    absent: list[StorageKey] = []
    invalid = 0
    for namespace, name in keys:
        try:
            key = StorageKey(namespace, name)
        except InvalidStorageKey:
            # A name the store cannot hold: the catalogue names bytes PRKS
            # cannot serve, and no release can make that a cleanup.
            invalid += 1
            continue
        try:
            found = storage.stat(key)
        except (InvalidStorageKey, StorageObjectError):
            # A link where the object should be: bytes PRKS cannot serve.
            found = None
        if found is None:
            absent.append(key)
    still_named = db.query(lambda tx: _still_named(tx, absent)) if absent else 0
    return ObjectCounts(referenced=len(keys), missing=invalid + still_named, **claims)


def _still_named(tx: Transaction, keys: list[StorageKey]) -> int:
    # After the stats: a key released and settled since the first read is
    # no longer named, so its absence is cleanup, not loss. Unknown counts.
    return sum(1 for key in keys if references(tx, key) is not False)
