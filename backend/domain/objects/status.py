"""Object counts for the storage status (storage-architecture §11.1).

Counts only, never key names: a minted name embeds a sanitized original
filename, so names are private data (``log_safety``).

- ``missing``: keys a catalogue row names whose object the store does not
  have. Every referenced key is stat-ed, once per status read; the read is
  the owner's, on demand, never scheduled.
- ``pending_cleanup``: claims a Library recorded when it stopped naming a
  key, waiting for ``objects.cleanup`` (§11.2).
- ``orphans``: claims the orphan sweep recorded for objects nothing ever
  adopted, waiting for the same settle.
- ``held``: claims a restore holds (``hold``); never settled automatically.

The counts cover the whole instance: one data root serves every Library
(postgresql-architecture §6.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString

from backend.db.database import Database, Transaction
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
    missing = 0
    for namespace, name in keys:
        try:
            found = storage.stat(StorageKey(namespace, name))
        except (InvalidStorageKey, StorageObjectError):
            # A name the store cannot hold, or a link where the object should
            # be: either way the catalogue names bytes PRKS cannot serve.
            found = None
        if found is None:
            missing += 1
    return ObjectCounts(referenced=len(keys), missing=missing, **claims)
