"""Typed HTTP boundary models and OpenAPI helpers for PRKS.

Pydantic lives here only. Domain/application functions receive plain Python
values; these models must not own sync conflict semantics, entity lifecycle,
canonical mutations, SQLite transactions, or research-domain rules.
"""

from backend.api_contract.errors import ApiErrorEnvelope, research_error_envelope
from backend.api_contract.openapi import positions_openapi_document

__all__ = (
    "ApiErrorEnvelope",
    "research_error_envelope",
    "positions_openapi_document",
)
