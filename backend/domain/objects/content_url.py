"""The URL a client reads a managed Asset's bytes from (#1427).

``/api/assets/<asset id>/content?v=<version>``: the Asset id is the
identity, and the version is the head of the bytes' SHA-256. An annotation
save or a replace keeps the Asset id but names new bytes, so the URL
changes with them and a browser or offline cache never serves the old
bytes under the new URL. The server serves a URL only while its ``v`` still
names the Asset's bytes: an earlier version reads as missing, never as the
newer bytes under the older URL.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

PREFIX = "/api/assets/"
SUFFIX = "/content"
VERSION_CHARS = 16

_URL = re.compile(r"^/api/assets/(AS-[0-9A-Za-z-]+)/content(?:\?v=[0-9a-f]{1,64})?$")


def asset_content_url(asset_id: str, sha256: Optional[str]) -> str:
    """The content URL of ``asset_id``, versioned by ``sha256`` when it is known."""
    url = PREFIX + quote(str(asset_id), safe="") + SUFFIX
    return f"{url}?v={sha256[:VERSION_CHARS]}" if sha256 else url


def is_current_version(version: Optional[str], sha256: Optional[str]) -> bool:
    """Whether a URL's ``v`` names the bytes ``sha256``; no ``v`` names whatever is current."""
    if version is None:
        return True
    return bool(sha256) and version == str(sha256)[:VERSION_CHARS]


def asset_id_from_url(url: object) -> Optional[str]:
    """The Asset id a content URL names; ``None`` for any other spelling."""
    if not isinstance(url, str):
        return None
    found = _URL.match(url)
    return found.group(1) if found else None
