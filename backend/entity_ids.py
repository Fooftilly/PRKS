"""Collision-resistant entity IDs for distributed creation.

Historical `generate_id` used eight UUID hex characters after the prefix.
That is 32 bits of entropy, which was acceptable when one process allocated
every id. Offline clients minting the same prefix independently must not copy
that scheme.

New ids are `{prefix}-` plus the full 32-character uppercase UUID hex. Existing
eight-hex ids remain valid forever and are never rewritten. Tests and fixtures
may still invent non-hex ids; those are not a production generator.

Do not infer record type from the prefix. Positions and Persons both use `P-`.
"""
import re
import uuid

HEX_CHARS = 32
LEGACY_HEX_CHARS = 8
PREFIX_RE = re.compile(r"^[A-Z]{1,3}$")
_HEX = r"([0-9A-F]{%d}|[0-9A-F]{%d})" % (LEGACY_HEX_CHARS, HEX_CHARS)
ID_RE = re.compile(r"^([A-Z]{1,3})-" + _HEX + r"$")


def generate(prefix: str) -> str:
    if not isinstance(prefix, str) or not PREFIX_RE.fullmatch(prefix):
        raise ValueError("invalid id prefix")
    return "%s-%s" % (prefix, uuid.uuid4().hex.upper())


def parse(value):
    if not isinstance(value, str):
        return None
    match = ID_RE.fullmatch(value)
    if not match:
        return None
    return match.group(1), match.group(2)


def is_generated(value, prefix=None) -> bool:
    """True for either historical 8-hex or current 32-hex generator output."""
    parsed = parse(value)
    if parsed is None:
        return False
    return prefix is None or parsed[0] == prefix


def is_distributed(value, prefix=None) -> bool:
    """True only for the collision-resistant 32-hex form."""
    parsed = parse(value)
    if parsed is None or len(parsed[1]) != HEX_CHARS:
        return False
    return prefix is None or parsed[0] == prefix
