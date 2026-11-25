"""Backup and restore wire shapes: ``/api/backups/*`` (#1431).

The archive checks (R0), the restore and its refusals stay in
``backend/domain/maintenance``; ``backend/api/maintenance.py`` maps them.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BackupProgressLine(BaseModel):
    """One NDJSON line of ``POST /api/backups/progress``; ``ready`` or ``failed`` is the last."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["snapshot", "objects", "ready", "failed"]
    percent: int = Field(ge=0, le=100)
    token: Optional[str] = Field(default=None, description="`ready` only: the download token.")
    filename: Optional[str] = Field(default=None, description="`ready` only: the archive's file name.")
    warnings: Optional[list[str]] = Field(default=None, description="`ready` only.")
    error: Optional[str] = Field(default=None, description="`failed` only: what to tell the user.")


class BackupRefusal(BaseModel):
    """A backup or restore refusal: ``reason`` is the stable code."""

    model_config = ConfigDict(extra="forbid")

    error: str
    reason: Optional[str] = None
    code: Optional[str] = None
    message: Optional[str] = None
    request_id: Optional[str] = None


class StagedSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: Optional[str] = Field(description="When the archive was made, from its manifest.")
    works: Optional[int]
    pdf_files: int
    persons: Optional[int]
    annotations: Optional[int]


class BackupStaged(BaseModel):
    """``POST /api/backups/stage``: the verified archive's summary and the token a restore names."""

    model_config = ConfigDict(extra="forbid")

    token: str
    verified: bool
    summary: StagedSummary
    warnings: list[str]


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: Optional[str] = Field(default=None, description="The staged archive's token.")
    confirm: Optional[str] = Field(default=None, description="Must be `RESTORE`.")


class Restored(BaseModel):
    """``POST /api/backups/restore``: the new epoch fences the browser's earlier pending work."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["restored"]
    epoch: int
    objects_held: int
    warnings: list[str]
