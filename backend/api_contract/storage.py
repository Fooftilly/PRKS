"""Storage status HTTP response DTOs (storage-architecture §11.1, #720).

Shape only. ``backend.api.storage`` builds the body from the bound root
(``backend.storage.status``), the object counts
(``backend.domain.objects.status``) and the database configuration.

The body carries host paths, so it is the Library owner's alone and is
never cached. Counts only, never key names. Read-only: under PostgreSQL the
data root is paired with the database (postgresql-architecture §11.1), so
the browser never changes it. Importing this module loads no PostgreSQL
driver.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StorageRoot(BaseModel):
    """Where the Library's files are, and what chose that place."""

    model_config = ConfigDict(extra="forbid")

    configured: str = Field(..., description="The root as it was configured, before links were resolved.")
    resolved: str = Field(..., description="The directory PRKS uses: the configured root with links resolved.")
    source: Optional[Literal["cli", "env", "config_file", "platform_default", "development_default"]] = Field(
        ..., description="Which setting selected the root (storage-architecture §5.2); null for a test binding."
    )
    editable_in_app: Literal[False] = Field(
        ...,
        description=(
            "Always false: the root is paired with the database, so it changes only on the host, "
            "with a storage move or a new installation and a Library restore."
        ),
    )


class StorageIdentity(BaseModel):
    """The root marker (prks-root.json) as it is now."""

    model_config = ConfigDict(extra="forbid")

    storage_root_id: Optional[str] = Field(..., description="SR- plus 32 hex; null when the marker cannot be read.")
    layout_version: Optional[int]
    state: Optional[str] = Field(..., description="The marker state; active for a served root.")
    relocation_state: str = Field(
        ..., description="none, or the phase of a storage move the marker records (unknown when it names none)."
    )
    backend_type: Literal["local"]


class StorageFilesystem(BaseModel):
    """Facts about the filesystem that holds the root, measured, not assumed."""

    model_config = ConfigDict(extra="forbid")

    free_bytes: Optional[int] = Field(..., ge=0)
    total_bytes: Optional[int] = Field(..., ge=0)
    filesystem_type: Optional[str] = Field(..., description="The type the platform reports, such as ext4 or apfs.")
    network: Literal["local", "network", "uncertain", "unknown"] = Field(
        ..., description="Whether the type is known to be a network mount (§7.4); FUSE types are uncertain."
    )
    case_sensitive: Optional[bool] = Field(..., description="Measured with probe files; null when not measured.")
    normalization: Optional[Literal["preserved", "insensitive", "normalized"]] = Field(
        ...,
        description=(
            "Unicode names: preserved (NFC and NFD are two names), insensitive (either finds the other), "
            "normalized (the filesystem rewrites names). Null when not measured."
        ),
    )
    directory_fsync: Optional[bool] = Field(
        ..., description="Whether directory fsync works here, as the root's first probe recorded it."
    )


class StorageObjects(BaseModel):
    """Counts across the instance; never key names."""

    model_config = ConfigDict(extra="forbid")

    referenced: int = Field(..., ge=0, description="Objects the catalogue names.")
    missing: int = Field(..., ge=0, description="Named objects the data root does not have.")
    pending_cleanup: int = Field(..., ge=0, description="Released objects waiting for cleanup.")
    orphans: int = Field(..., ge=0, description="Objects nothing ever named, found by the orphan sweep, waiting for cleanup.")
    held: int = Field(..., ge=0, description="Objects a restore keeps; never removed automatically.")


class StorageDatabase(BaseModel):
    """The database half of the Library (storage-architecture §9.2), read-only."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["PostgreSQL"]
    host: Optional[str] = Field(..., description="Host name or address; 'local socket' for a Unix socket.")
    port: Optional[str]
    name: Optional[str] = Field(..., description="The database name.")


class StorageStatus(BaseModel):
    """GET /api/storage-status body (Library owner only, never cached)."""

    model_config = ConfigDict(extra="forbid")

    root: StorageRoot
    identity: StorageIdentity
    filesystem: StorageFilesystem
    objects: Optional[StorageObjects] = Field(
        ..., description="Null when the counts could not be read (the database or the store was unavailable)."
    )
    database: StorageDatabase
