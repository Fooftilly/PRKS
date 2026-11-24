"""Canonical Tag color grammar for API and durable CREATE_TAG writes.

Raw data from imports or older revisions may remain, so renderers still apply
a separate allowlist when using colors in CSS style attributes.
"""
from __future__ import annotations

import re

DEFAULT_COLOR = "#6d6cf7"
_SAFE_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]?|[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)\Z")


def normalized_tag_color(value: object, *, blank_defaults: bool = False) -> str:
    """Accept CSS hex #RGB/#RGBA/#RRGGBB/#RRGGBBAA, nothing executable."""
    if blank_defaults and (value is None or isinstance(value, str) and not value.strip()):
        return DEFAULT_COLOR
    if not isinstance(value, str):
        raise ValueError("invalid tag color")
    color = value.strip()
    if not _SAFE_COLOR.fullmatch(color):
        raise ValueError("invalid tag color")
    return color
