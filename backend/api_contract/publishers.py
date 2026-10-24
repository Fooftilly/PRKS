"""Publishers HTTP request/response DTOs.

Shape and JSON types only. Name and alias emptiness, the canonical-name and
alias conflict rules, and the in-use catalog stay in ``db_manager``. Publishers
are online-only: there is no durable publisher operation and no sync state.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PublisherInUse(BaseModel):
    """One row of GET /api/publishers?used=1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    aliases: list[str]
    work_count: int = Field(..., ge=0)


class PublisherCreateRequest(BaseModel):
    """POST /api/publishers. Emptiness is a domain refusal, not a schema one."""

    model_config = ConfigDict(extra="ignore")

    name: str


class PublisherCreated(BaseModel):
    """POST /api/publishers body. ``existed`` is true when the name was already canonical."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    existed: bool


class PublisherAliasRequest(BaseModel):
    """POST /api/publishers/{id}/aliases."""

    model_config = ConfigDict(extra="ignore")

    alias: str


class PublisherAliasAdded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^added$")


class PublisherDeleted(BaseModel):
    """DELETE /api/publishers/{id} and DELETE /api/publishers/{id}/aliases body."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^deleted$")
