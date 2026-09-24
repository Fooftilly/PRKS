"""Positions HTTP request/response DTOs.

Shape and JSON types only. Name emptiness, control characters, length limits,
POSITION_IN_USE, and revision rules stay in ``research_network`` /
``position_sync``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class PositionCreateRequest(BaseModel):
    """POST /api/positions body."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Position claim name (domain-normalized).")
    description: Optional[str] = Field(
        default=None,
        description="Optional markdown description; domain enforces limits.",
    )


class PositionUpdateRequest(BaseModel):
    """PATCH /api/positions/{id} body.

    Omitted fields are not updated. Whether at least one field is supplied is a
    domain rule (``nothing_to_update``), not enforced here.
    """

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = None
    description: Optional[str] = None

    def domain_field_kwargs(self) -> dict[str, Any]:
        """Pass only keys the client actually sent."""
        out: dict[str, Any] = {}
        if "name" in self.model_fields_set:
            out["name"] = self.name
        if "description" in self.model_fields_set:
            out["description"] = self.description
        return out


class PositionArgumentSummary(BaseModel):
    """Argument/Stance row embedded on Position detail."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    kind: str
    verdict_id: str
    verdict_label: str


class PositionSummary(BaseModel):
    """Index row from GET /api/positions (no embedded arguments)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PositionDetail(BaseModel):
    """Detail / create / update response including Arguments targeting this Position."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    arguments: list[PositionArgumentSummary] = Field(default_factory=list)


class PositionDeleted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern=r"^deleted$")


class PositionSyncFieldState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(..., ge=0)


class PositionSyncState(BaseModel):
    """GET /api/positions/{id}/sync-state — revisions only."""

    model_config = ConfigDict(extra="ignore")

    position_id: str
    fields: dict[str, PositionSyncFieldState]
