"""Parse/serialize helpers for the HTTP adapter.

Domain functions receive ordinary values (``str``, ``dict``, …), never Pydantic
model instances used as business state.
"""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.api_contract.errors import validation_error_envelope

T = TypeVar("T", bound=BaseModel)


def parse_request(model_cls: type[T], data: Any) -> tuple[T | None, dict[str, Any] | None]:
    """Validate a JSON body into a request model.

    Returns ``(model, None)`` on success or ``(None, error_envelope)`` on failure.
    """
    if not isinstance(data, dict):
        return None, {
            "error": "JSON object body required",
            "code": "invalid_request",
        }
    try:
        return model_cls.model_validate(data), None
    except ValidationError as exc:
        return None, validation_error_envelope(exc)


def dump_response(model_cls: type[T], payload: Any) -> dict[str, Any] | list[Any]:
    """Validate a domain result against a response model and return plain JSON.

    Accepts a single object or a list (for index endpoints).
    """
    if isinstance(payload, list):
        return [model_cls.model_validate(item).model_dump(mode="json") for item in payload]
    return model_cls.model_validate(payload).model_dump(mode="json")


def domain_kwargs_from(model: BaseModel, *, fields: tuple[str, ...]) -> dict[str, Any]:
    """Extract plain field values for domain calls (only fields present / set)."""
    data = model.model_dump(mode="python")
    return {name: data[name] for name in fields if name in data}
