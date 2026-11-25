"""Typed HTTP boundary models and the OpenAPI document for PRKS.

Pydantic lives here only. Domain/application functions receive plain Python
values; these models must not own sync conflict semantics, entity lifecycle,
canonical mutations, database transactions, or research-domain rules.
Routes declare their contract (``operation.py``); ``openapi.py`` builds the
one document from the route table.
"""

from backend.api_contract.errors import ApiErrorEnvelope, research_error_envelope

__all__ = (
    "ApiErrorEnvelope",
    "research_error_envelope",
)
