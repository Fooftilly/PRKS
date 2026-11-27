"""Backup and restore wire shapes: ``/api/backups/*`` (#1431).

The archive checks (R0), the restore and its refusals stay in
``backend/domain/maintenance``; ``backend/api/maintenance.py`` maps them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel


class BackupWorking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["snapshot", "objects"]
    percent: int = Field(ge=0, le=100)


class BackupReady(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["ready"]
    percent: int = Field(ge=0, le=100)
    token: str = Field(description="The download token.")
    filename: str = Field(description="The archive's file name.")
    warnings: list[str]


class BackupFailed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["failed"]
    percent: int = Field(ge=0, le=100)
    error: str = Field(description="What to tell the user.")


class BackupProgressLine(RootModel[Annotated[Union[BackupWorking, BackupReady, BackupFailed],
                                             Field(discriminator="phase")]]):
    """One NDJSON line of ``POST /api/backups/progress``; ``ready`` or ``failed`` is the last."""


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

    token: str = Field(description="The staged archive's token.")
    confirm: Literal["RESTORE"] = Field(description="Confirms the restore over the Library.")


class Restored(BaseModel):
    """``POST /api/backups/restore``: the new epoch fences the browser's earlier pending work."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["restored"]
    epoch: int
    objects_held: int
    warnings: list[str]
