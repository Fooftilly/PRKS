"""Common API error envelope for typed HTTP boundaries.

Wire shape matches existing research-network responses:
``{"error": "<message>", "code": "<stable_code>"}`` with ``code`` optional for
legacy 404 bodies that only carried ``error``. Admission and route refusals
(``backend/api/session.py``) answer ``{"error": code, "code": code,
"message": text}``, so ``message`` is an optional field too.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ApiErrorEnvelope(BaseModel):
    """Standard JSON error body at the HTTP adapter boundary."""

    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., min_length=1, description="Human-readable error message.")
    code: Optional[str] = Field(
        default=None,
        description="Stable machine-readable code when the domain provides one.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Human-readable explanation sent with admission and route refusals.",
    )

    def as_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        return data


class NotPortedEnvelope(BaseModel):
    """``501 not_ported`` (``backend.web.dispatch.not_ported``): a route PostgreSQL does not serve yet."""

    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., min_length=1)
    code: Literal["not_ported"]
    request_id: str


def research_error_envelope(*, message: str, code: str | None = None) -> dict[str, Any]:
    """Build the common error dict without importing ResearchError here."""
    return ApiErrorEnvelope(error=message, code=code).as_dict()


def validation_error_envelope(exc: BaseException) -> dict[str, Any]:
    """Map a Pydantic ValidationError (or similar) to the common envelope.

    Does not embed field paths or raw input values (privacy / log-safety).
    """
    return ApiErrorEnvelope(
        error="Request body failed schema validation.",
        code="invalid_request",
    ).as_dict()
