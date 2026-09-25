"""Positions HTTP request/response DTOs.

Shape and JSON types only. Name emptiness, control characters, length limits,
POSITION_IN_USE, and revision rules stay in ``research_network`` /
``position_sync``.

Wrong-type ``name`` / ``description`` map to the established domain codes
(``invalid_name`` / ``invalid_text``) so the typed boundary does not change
existing Position API error semantics.
"""
from __future__ import annotations

from typing import Any, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api_contract.errors import (
    research_error_envelope,
    validation_error_envelope,
)

T = TypeVar("T", bound=BaseModel)

# Domain messages for type refusals — keep in lockstep with
# research_network._plain_name / _optional_markdown.
_NAME_MUST_BE_STRING = "Name must be a string."
_TEXT_MUST_BE_STRING = "Text must be a string."


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
        """Pass only keys the client actually sent.

        Explicit JSON ``null`` is forwarded as Python ``None``. The domain
        treats ``None`` as the omit sentinel (``nothing_to_update`` when it is
        the only field; otherwise that field is left unchanged). Clear-on-null
        is deliberately **not** part of this #180 slice — see #45 if that
        contract is approved later. Absent keys stay omitted.
        """
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


def position_validation_error_envelope(exc: ValidationError) -> dict[str, Any]:
    """Map Pydantic failures onto established Position domain codes when possible.

    ``name`` / ``description`` type (and create missing-name) refusals historically
    came from ``research_network`` as ``invalid_name`` / ``invalid_text``. Preserve
    those codes/messages at the typed boundary so clients do not see a generic
    ``invalid_request`` for fields the domain already owned.
    """
    for err in exc.errors():
        loc = err.get("loc") or ()
        if not loc or not isinstance(loc[0], str):
            continue
        field = loc[0]
        etype = err.get("type") or ""
        if field == "name" and etype in ("string_type", "missing"):
            return research_error_envelope(
                message=_NAME_MUST_BE_STRING,
                code="invalid_name",
            )
        if field == "description" and etype == "string_type":
            return research_error_envelope(
                message=_TEXT_MUST_BE_STRING,
                code="invalid_text",
            )
    return validation_error_envelope(exc)


def parse_position_request(
    model_cls: type[T], data: Any
) -> tuple[T | None, dict[str, Any] | None]:
    """Parse a Positions create/update body with domain-preserving type errors."""
    if not isinstance(data, dict):
        return None, {
            "error": "JSON object body required",
            "code": "invalid_request",
        }
    try:
        return model_cls.model_validate(data), None
    except ValidationError as exc:
        return None, position_validation_error_envelope(exc)
