"""Saved View validation shared by every Library backend (PG-C2 #641).

Name and search limits and the API row shape. Pure: no store. Moved from
``backend/db_manager.py``.
"""

from __future__ import annotations

import re
from typing import Any, Dict


class SavedViewError(ValueError):
    """Controlled Saved View failure. http_status is 400, 404, or 409."""

    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.http_status = http_status


PRKS_SAVED_VIEW_MAX = 100


PRKS_SAVED_VIEW_NAME_MAX = 80


PRKS_SAVED_VIEW_Q_MAX = 500


PRKS_SAVED_VIEW_FIELD_MAX = 200


_SAVED_VIEW_MODES = frozenset({"all", "advanced", "tag"})


_SAVED_VIEW_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _saved_view_has_controls(value: str) -> bool:
    return bool(_SAVED_VIEW_CONTROL_RE.search(value))


def normalize_saved_view_name(raw) -> str:
    if not isinstance(raw, str):
        raise SavedViewError("Name must be a string.")
    name = raw.strip()
    if not name:
        raise SavedViewError("Name is required.")
    if _saved_view_has_controls(name):
        raise SavedViewError("Name contains invalid characters.")
    if len(name) > PRKS_SAVED_VIEW_NAME_MAX:
        raise SavedViewError("Name is too long.")
    return name


def normalize_saved_view_search(search) -> Dict[str, str]:
    if not isinstance(search, dict):
        raise SavedViewError("Search definition is required.")
    for key in ("mode", "q", "tag", "author", "publisher"):
        if key not in search:
            raise SavedViewError("Search definition is incomplete.")
    mode_raw = search.get("mode")
    if not isinstance(mode_raw, str):
        raise SavedViewError("Invalid search mode.")
    mode = mode_raw.strip()
    if mode not in _SAVED_VIEW_MODES:
        raise SavedViewError("Invalid search mode.")

    def _field(key: str, max_len: int) -> str:
        value = search.get(key)
        if not isinstance(value, str):
            raise SavedViewError("Search fields must be strings.")
        text = value.strip()
        if _saved_view_has_controls(text):
            raise SavedViewError("Search definition contains invalid characters.")
        if len(text) > max_len:
            raise SavedViewError("Search field is too long.")
        return text

    q = _field("q", PRKS_SAVED_VIEW_Q_MAX)
    tag = _field("tag", PRKS_SAVED_VIEW_FIELD_MAX)
    author = _field("author", PRKS_SAVED_VIEW_FIELD_MAX)
    publisher = _field("publisher", PRKS_SAVED_VIEW_FIELD_MAX)
    if mode == "all":
        if not q or tag or author or publisher:
            raise SavedViewError("All-fields views require a query and no other filters.")
    elif mode == "advanced":
        if tag:
            raise SavedViewError("Advanced views cannot include a tag.")
        if not (q or author or publisher):
            raise SavedViewError("Advanced views require keywords, author, or publisher.")
    else:
        if not tag or q:
            raise SavedViewError("Tag views require a tag and no keyword query.")
    return {
        "mode": mode,
        "q": q,
        "tag": tag,
        "author": author,
        "publisher": publisher,
    }


def saved_view_api_row(row: dict) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "search": {
            "mode": row["mode"],
            "q": row["search_q"],
            "tag": row["search_tag"],
            "author": row["search_author"],
            "publisher": row["search_publisher"],
        },
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
