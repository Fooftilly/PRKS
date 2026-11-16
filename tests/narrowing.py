"""Small helpers that narrow optional values for the type checker in tests.

Each one fails the test (``AssertionError``) where the old code would have
raised ``AttributeError``/``TypeError`` on ``None``.
"""

from __future__ import annotations

import re
from typing import TypeVar

T = TypeVar("T")


def must_search(pattern: str | re.Pattern[str], string: str, flags: int = 0) -> re.Match[str]:
    """``re.search`` that fails the test instead of returning ``None``."""
    match = re.search(pattern, string, flags)
    if match is None:
        raise AssertionError(f"pattern {pattern!r} not found")
    return match


def require(value: T | None, what: str = "value") -> T:
    """Fail the test when ``value`` is ``None``; otherwise return it narrowed."""
    if value is None:
        raise AssertionError(f"{what} is None")
    return value
